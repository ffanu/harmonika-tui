"""Harmonika RTX Gateway — unified AI API for the local GPU server.

Satu endpoint untuk semua engine (gambar / video / suara):
  POST /v1/jobs        {skill_id, input} + Idempotency-Key + Bearer
  GET  /v1/jobs/{id}
  GET  /files/{name}   hasil (png/wav/mp4)
  GET  /health
Serial GPU queue; hasil disimpan di ./output dan dilayani sebagai file.
"""
import asyncio, base64, hashlib, hmac, json, mimetypes, os, re, secrets, time, uuid
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)
CFG = json.loads((ROOT / "config.json").read_text())
TOKENS = set([CFG["token"]]) | set(CFG.get("api_keys", {}).values())

COMFY = "http://127.0.0.1:18190"
KLEIN = "http://127.0.0.1:18181"
FISH = "http://127.0.0.1:8085"
MEDIA = "http://127.0.0.1:18231"
VISION = "http://127.0.0.1:18188"
MEDIA_TOKEN = CFG["media_token"]
VISION_TOKEN = CFG["vision_token"]

NEG_WAN = "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"

SKILLS = ("image.generate", "image.edit", "image.analyze", "image.upscale",
          "video.generate", "audio.tts", "audio.transcribe", "audio.music")


class Err(Exception):
    def __init__(self, code, status=400):
        self.code = code; self.status = status


# ---------- engine helpers ----------

async def http_json(method, url, **kw):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=kw.pop("timeout", 900))) as s:
        async with s.request(method, url, **kw) as r:
            body = await r.read()
            if r.status not in (200, 202):
                raise Err("engine_upstream_%d:%s" % (r.status, body[:120].decode(errors="replace")), 502)
            return json.loads(body)


async def comfy_run(wf, timeout=3600):
    """Submit ComfyUI workflow, wait, save artifact to OUT, return filename."""
    pid = (await http_json("POST", COMFY + "/prompt", json={"prompt": wf, "client_id": "gw"},
                           headers={"Content-Type": "application/json"}))["prompt_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = await http_json("GET", COMFY + "/history/" + pid, timeout=60)
        if pid in h:
            item = h[pid]
            if item.get("status", {}).get("status_str") == "error":
                raise Err("render_failed", 502)
            for node in item.get("outputs", {}).values():
                for kind in ("gifs", "videos", "images", "audio"):
                    for art in node.get(kind, []):
                        url = "%s/view?filename=%s&subfolder=%s&type=%s" % (
                            COMFY, art["filename"], art.get("subfolder", ""), art["type"])
                        name = "%s_%s" % (re.sub(r"[^a-z0-9]+", "_", art["filename"].lower())[:40], uuid.uuid4().hex[:6])
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
                            async with s.get(url) as r:
                                data = await r.read()
                        (OUT / name).write_bytes(data)
                        try:
                            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s2:
                                await s2.post(COMFY + "/free", json={"unload_models": True, "free_memory": True})
                        except Exception:
                            pass
                        return {"file": name, "bytes": len(data)}
        await asyncio.sleep(4)
    raise Err("render_timeout", 504)


def wf_flux(prompt, w, h, steps, seed):
    return {
      "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev-fp8.safetensors", "weight_dtype": "default"}},
      "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp8_e4m3fn.safetensors", "type": "flux"}},
      "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux-vae-bf16.safetensors"}},
      "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
      "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
      "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": 3.5}},
      "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
      "8": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["1", 0], "positive": ["6", 0], "negative": ["5", 0], "latent_image": ["7", 0]}},
      "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
      "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "gw_img"}},
    }


def wf_kontext(image_name, instruction, seed):
    return {
      "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-kontext-dev_fp8_scaled_stochastic-marduk191.safetensors", "weight_dtype": "default"}},
      "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp8_e4m3fn.safetensors", "type": "flux"}},
      "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux-vae-bf16.safetensors"}},
      "4": {"class_type": "LoadImage", "inputs": {"image": image_name}},
      "5": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
      "5b": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
      "6": {"class_type": "CLIPTextEncode", "inputs": {"text": instruction, "clip": ["2", 0]}},
      "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
      "8": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["6", 0], "guidance": 2.5}},
      "9": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["8", 0]}},
      "10": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "steps": 20, "denoise": 1.0, "scheduler": "simple"}},
      "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
      "12": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
      "13": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["12", 0], "guider": ["9", 0], "sampler": ["11", 0], "sigmas": ["10", 0], "latent_image": ["5b", 0]}},
      "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
      "15": {"class_type": "SaveImage", "inputs": {"images": ["14", 0], "filename_prefix": "gw_edit"}},
    }


def wf_wan14b(image_name, prompt, seed, length, w, h):
    nodes = {
      "84": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
      "90": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
      "93": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["84", 0]}},
      "89": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG_WAN, "clip": ["84", 0]}},
      "96": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}},
      "102": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "strength_model": 1.0, "model": ["96", 0]}},
      "103": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["102", 0], "shift": 5.0}},
      "95": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}},
      "101": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "strength_model": 1.0, "model": ["95", 0]}},
      "104": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["101", 0], "shift": 5.0}},
      "85": {"class_type": "KSamplerAdvanced", "inputs": {"add_noise": "enable", "noise_seed": seed, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "start_at_step": 0, "end_at_step": 2, "return_with_leftover_noise": "enable", "model": ["103", 0], "positive": ["98", 0], "negative": ["98", 1], "latent_image": ["98", 2]}},
      "86": {"class_type": "KSamplerAdvanced", "inputs": {"add_noise": "disable", "noise_seed": seed, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "start_at_step": 2, "end_at_step": 10, "return_with_leftover_noise": "disable", "model": ["104", 0], "positive": ["98", 0], "negative": ["98", 1], "latent_image": ["85", 0]}},
      "87": {"class_type": "VAEDecode", "inputs": {"samples": ["86", 0], "vae": ["90", 0]}},
      "110": {"class_type": "CreateVideo", "inputs": {"fps": 16, "bit_depth": 8, "color_space": "sRGB", "codec": "none", "images": ["87", 0]}},
      "111": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "gw_vid", "format": "auto", "format.codec": "auto", "codec": "auto", "video": ["110", 0]}},
    }
    if image_name:
        nodes["87i"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        nodes["98"] = {"class_type": "WanImageToVideo", "inputs": {"positive": ["93", 0], "negative": ["89", 0], "vae": ["90", 0], "start_image": ["87i", 0], "width": w, "height": h, "length": length, "batch_size": 1}}
    else:
        nodes["98"] = {"class_type": "WanImageToVideo", "inputs": {"positive": ["93", 0], "negative": ["89", 0], "vae": ["90", 0], "width": w, "height": h, "length": length, "batch_size": 1}}
    return nodes


def wf_music(caption, seconds, seed):
    return {
      "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_music3_dit_fp16.safetensors", "weight_dtype": "default"}},
      "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "minimax_music3_text_encoder_pruned_int8_convrot.safetensors", "type": "minimax", "device": "default"}},
      "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_music3_dav.safetensors"}},
      "4": {"class_type": "MiniMaxMusic3TextEncode", "inputs": {"clip": ["2", 0], "caption": caption, "lyrics": "", "seed": seed, "max_duration": seconds + 2.0, "cfg_scale": 1.7, "top_k": 50}},
      "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
      "6": {"class_type": "EmptyMiniMaxMusic3LatentAudio", "inputs": {"seconds": seconds + 2.0, "batch_size": 1}},
      "7": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 30, "cfg": 1.7, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0]}},
      "8": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
      "9": {"class_type": "SaveAudio", "inputs": {"audio": ["8", 0], "filename_prefix": "gw_music"}},
    }


async def comfy_upload(image_b64, name):
    data = base64.b64decode(image_b64)
    b = "----gw" + uuid.uuid4().hex
    body = (("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n" % (b, name)).encode()
            + data + ("\r\n--%s--\r\n" % b).encode())
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        async with s.post(COMFY + "/upload/image", data=body,
                          headers={"Content-Type": "multipart/form-data; boundary=" + b}) as r:
            if r.status != 200:
                raise Err("image_upload_failed", 502)
            return (await r.json())["name"]


# ---------- skill handlers ----------

def wf_qwen2512(prompt, w, h, seed):
    return {
      "219": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
      "220": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
      "226": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2512_fp8_e4m3fn.safetensors", "weight_dtype": "default"}},
      "221": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors", "strength_model": 1.0, "model": ["226", 0]}},
      "222": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["221", 0], "shift": 3.1}},
      "227": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["219", 0]}},
      "228": {"class_type": "CLIPTextEncode", "inputs": {"text": "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，模糊，文字错误，拼写错误，乱码", "clip": ["219", 0]}},
      "232": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
      "230": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
          "model": ["222", 0], "positive": ["227", 0], "negative": ["228", 0], "latent_image": ["232", 0]}},
      "231": {"class_type": "VAEDecode", "inputs": {"samples": ["230", 0], "vae": ["220", 0]}},
      "60": {"class_type": "SaveImage", "inputs": {"images": ["231", 0], "filename_prefix": "gw_qwen"}},
    }


TEXT_SIGNALS = ("poster", "plakat", "banner", "headline", "typography", "typografi", "logo text",
                "quote", "slogan", "tagline", "spelled", "reads", "teks", "text reads", "caption",
                "label", "price list", "menu", "undangan", "certificate", "sertifikat", "iklan")
REALISM_SIGNALS = ("photo", "foto", "fotorealis", "realistic", "cinematic", "portrait", "potret",
                   "landscape", "produk", "product shot", "render", "8k", "studio", "wallpaper")


def pick_image_engine(prompt):
    p = prompt.lower()
    if '"' in prompt or any(k in p for k in TEXT_SIGNALS):
        return "qwen2512", 60
    return "flux", 90


async def run_skill(skill, inp):
    if skill == "image.generate":
        engine = inp.get("engine", "auto")
        if engine == "auto":
            engine, est = pick_image_engine(inp["prompt"])
        if engine == "klein":
            r = await http_json("POST", KLEIN + "/generate", json={
                "prompt": inp["prompt"], "width": inp.get("width", 1024), "height": inp.get("height", 1024)})
            name = "gw_klein_" + uuid.uuid4().hex[:8] + ".png"
            (OUT / name).write_bytes(base64.b64decode(r["b64_png"]))
            return {"file": name, "engine": "flux2-klein", "seconds": r.get("seconds")}
        if engine == "qwen2512":
            wf = wf_qwen2512(inp["prompt"], inp.get("width", 1088), inp.get("height", 1600),
                             inp.get("seed") or secrets.randbits(48))
            out = await comfy_run(wf)
            out["engine"] = "qwen-image-2512"; return out
        wf = wf_flux(inp["prompt"], inp.get("width", 1024), inp.get("height", 1024),
                     inp.get("steps", 24), inp.get("seed") or secrets.randbits(48))
        out = await comfy_run(wf)
        out["engine"] = "flux1-dev"; return out

    if skill == "image.edit":
        name = await comfy_upload(inp["image_base64"], "gw_edit_in.png")
        wf = wf_kontext(name, inp["instruction"], inp.get("seed") or secrets.randbits(48))
        return await comfy_run(wf)

    if skill == "video.generate":
        img = inp.get("image_base64")
        name = await comfy_upload(img, "gw_vid_in.png") if img else None
        seconds = min(10, max(3, inp.get("seconds", 5)))
        length = 4 * round(seconds * 4) + 1
        wf = wf_wan14b(name, inp["prompt"], inp.get("seed") or secrets.randbits(31), length,
                       inp.get("width", 1280), inp.get("height", 704))
        return await comfy_run(wf)

    if skill == "audio.tts":
        import socket as _s, subprocess as _sp
        def _port_open():
            try:
                with _s.create_connection(("127.0.0.1", 8085), timeout=2):
                    return True
            except OSError:
                return False
        if not _port_open():
            _sp.run(["schtasks", "/run", "/tn", "HarmonikaFishApi"], capture_output=True)
            for _ in range(120):
                if _port_open():
                    break
                await asyncio.sleep(3)
            await asyncio.sleep(45)
        payload = {"text": inp["text"], "format": "wav", "normalize": True, "chunk_length": 200}
        if inp.get("reference_audio_base64"):
            payload["references"] = [{"audio": inp["reference_audio_base64"], "text": inp.get("reference_text", "")}]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900)) as s:
            async with s.post(FISH + "/v1/tts", json=payload) as r:
                if r.status != 200:
                    raise Err("tts_failed:" + (await r.text())[:120], 502)
                data = await r.read()
        name = "gw_tts_" + uuid.uuid4().hex[:8] + ".wav"
        (OUT / name).write_bytes(data)
        return {"file": name, "bytes": len(data)}

    if skill == "audio.music":
        return await comfy_run(wf_music(inp["description"], min(60, max(5, inp.get("seconds", 12))),
                                        inp.get("seed") or 424242))

    if skill == "audio.transcribe":
        async def proxy():
            idem = "gw-" + uuid.uuid4().hex[:16]
            r = await http_json("POST", MEDIA + "/v1/jobs", json={
                "skill_id": "audio.transcribe",
                "input": {"audio_base64": inp["audio_base64"], "language": inp.get("language", "id")}},
                headers={"Authorization": "Bearer " + MEDIA_TOKEN, "Idempotency-Key": idem}, timeout=120)
            jid = r["job_id"]
            for _ in range(400):
                await asyncio.sleep(3)
                r = await http_json("GET", MEDIA + "/v1/jobs/" + jid,
                                    headers={"Authorization": "Bearer " + MEDIA_TOKEN}, timeout=60)
                if r.get("state") in ("completed", "failed", "unknown"):
                    if r.get("state") == "completed":
                        return r["result"]
                    raise Err("asr_failed", 502)
            raise Err("asr_timeout", 504)
        return await proxy()

    if skill == "image.upscale":
        async def proxy2():
            idem = "gw-" + uuid.uuid4().hex[:16]
            r = await http_json("POST", MEDIA + "/v1/jobs", json={
                "skill_id": "image.upscale", "input": {"image_base64": inp["image_base64"]}},
                headers={"Authorization": "Bearer " + MEDIA_TOKEN, "Idempotency-Key": idem}, timeout=120)
            jid = r["job_id"]
            for _ in range(200):
                await asyncio.sleep(3)
                r = await http_json("GET", MEDIA + "/v1/jobs/" + jid,
                                    headers={"Authorization": "Bearer " + MEDIA_TOKEN}, timeout=60)
                if r.get("state") in ("completed", "failed", "unknown"):
                    if r.get("state") == "completed":
                        res = r.get("result", {})
                        for k in ("image_base64", "upscaled_base64", "output_base64", "b64_png"):
                            if isinstance(res.get(k), str) and len(res[k]) > 100:
                                name = "gw_up_" + uuid.uuid4().hex[:8] + ".png"
                                (OUT / name).write_bytes(base64.b64decode(res[k]))
                                return {"file": name}
                        return res
                    raise Err("upscale_failed", 502)
            raise Err("upscale_timeout", 504)
        return await proxy2()

    if skill == "image.analyze":
        async def proxy3():
            img = base64.b64decode(inp["image_base64"])
            import hashlib as H
            job_id = "job_gw" + uuid.uuid4().hex[:12]
            digest = H.sha256((job_id + str(time.time())).encode()).hexdigest()
            meta = {"schema_version": 1, "kind": "analyze", "job_id": job_id, "request_digest": digest,
                    "prompt": inp.get("prompt") or "Describe this image and transcribe all visible text.",
                    "reference_files": [{"field": "reference_0", "mime_type": "image/png",
                                         "size_bytes": len(img), "sha256": H.sha256(img).hexdigest()}]}
            b = "----gw" + uuid.uuid4().hex
            body = (("--%s\r\nContent-Disposition: form-data; name=\"job\"\r\nContent-Type: application/json\r\n\r\n" % b).encode()
                    + json.dumps(meta).encode() + b"\r\n")
            body += (("--%s\r\nContent-Disposition: form-data; name=\"reference_0\"\r\nContent-Type: image/png\r\n\r\n" % b).encode()
                     + img + b"\r\n")
            body += ("--%s--\r\n" % b).encode()
            r = await http_json("POST", VISION + "/v1/rtx/jobs", data=body, timeout=180,
                                headers={"Authorization": "Bearer " + VISION_TOKEN,
                                         "Idempotency-Key": job_id,
                                         "Content-Type": "multipart/form-data; boundary=" + b})
            for _ in range(300):
                await asyncio.sleep(2)
                r = await http_json("GET", VISION + "/v1/rtx/jobs/" + job_id,
                                    headers={"Authorization": "Bearer " + VISION_TOKEN}, timeout=60)
                if r.get("status") == "completed":
                    return {"text": r["result"]["text"]}
                if r.get("status") in ("failed", "unknown"):
                    raise Err("vision_failed", 502)
            raise Err("vision_timeout", 504)
        return await proxy3()

    raise Err("skill_not_found", 404)


# ---------- app ----------

JOBS = {}
IDEM = {}
QUEUE = None


def check_auth(req):
    auth = req.headers.get("Authorization", "")
    if not any(hmac.compare_digest(auth, "Bearer " + t) for t in TOKENS):
        raise web.HTTPUnauthorized(text="invalid token")


async def submit(req):
    check_auth(req)
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": {"code": "invalid_json"}}, status=400)
    skill = body.get("skill_id")
    if skill not in SKILLS:
        return web.json_response({"error": {"code": "skill_not_found", "skills": SKILLS}}, status=404)
    idem = req.headers.get("Idempotency-Key", "")
    if idem:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", idem):
            return web.json_response({"error": {"code": "invalid_idempotency_key"}}, status=400)
        if idem in IDEM:
            return web.json_response({"job_id": IDEM[idem], "state": JOBS[IDEM[idem]]["state"]})
    jid = "gw_" + uuid.uuid4().hex[:16]
    JOBS[jid] = {"skill_id": skill, "state": "queued", "result": None, "error": None, "created": time.time()}
    if idem:
        IDEM[idem] = jid
    await QUEUE.put((jid, body.get("input", {})))
    return web.json_response({"job_id": jid, "state": "queued"}, status=202)


async def get_job(req):
    check_auth(req)
    j = JOBS.get(req.match_info["jid"])
    if not j:
        return web.json_response({"error": {"code": "job_not_found"}}, status=404)
    out = {"job_id": req.match_info["jid"], "state": j["state"], "skill_id": j["skill_id"]}
    if j["state"] == "completed":
        res = dict(j["result"] or {})
        if res.get("file"):
            res["url"] = "/files/" + res["file"]
        out["result"] = res
    if j["error"]:
        out["error"] = {"code": j["error"]}
    return web.json_response(out)


async def files(req):
    check_auth(req)
    name = os.path.basename(req.match_info["name"])
    p = OUT / name
    if not p.is_file():
        return web.json_response({"error": {"code": "not_found"}}, status=404)
    return web.FileResponse(p)


async def health(req):
    return web.json_response({"ok": True, "skills": SKILLS, "pending": QUEUE.qsize() if QUEUE else 0})


async def worker(app):
    while True:
        jid, inp = await QUEUE.get()
        j = JOBS.get(jid)
        if not j:
            continue
        j["state"] = "running"
        try:
            j["result"] = await run_skill(j["skill_id"], inp)
            j["state"] = "completed"
        except Err as e:
            j["state"] = "failed"; j["error"] = e.code
        except Exception as e:
            j["state"] = "failed"; j["error"] = "internal:" + str(e)[:160]


async def start(app):
    global QUEUE
    QUEUE = asyncio.Queue(maxsize=8)
    app["worker"] = asyncio.create_task(worker(app))


async def stop(app):
    app["worker"].cancel()


def create_app():
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.add_routes([web.post("/v1/jobs", submit),
                    web.get("/v1/jobs/{jid}", get_job),
                    web.get("/files/{name}", files),
                    web.get("/health", health)])
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=CFG.get("port", 18200), access_log=None, print=None)
