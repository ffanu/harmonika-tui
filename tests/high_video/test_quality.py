import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[2]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/'backend/high_video'/file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


wf = load('quality_wf_tests', 'workflow.py')
proxy = load('quality_proxy_tests', 'proxy.py')
coord = load('quality_coordinator_tests', 'coordinator.py')
qualification = load('quality_evidence_tests', 'qualify.py')
quality_broker = load('quality_broker_tests', 'broker.py')
spec = importlib.util.spec_from_file_location('quality_legacy_tests', ROOT/'backend/video_toolkit/broker_av.py')
legacy_broker = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy_broker)


class QualityContractTests(unittest.TestCase):
    def test_real_quality_model_workflows_and_windows_dropdown_names(self):
        for family in ('wan22_5b', 'wan22_14b'):
            nodes = wf.build(family, 'Synthetic fixture', 10, 'a'*32)
            self.assertEqual(nodes['1']['inputs']['device'], 'default')
            self.assertTrue(nodes['1']['inputs']['clip_name'].startswith('harmonika-quality\\'))
            self.assertEqual(nodes['7']['inputs']['width'], 1280)
            self.assertGreaterEqual(nodes['7']['inputs']['height'], 720)
            self.assertGreaterEqual(nodes['7']['inputs']['length'], 10*wf.profile(family)['fps'])
            self.assertEqual(nodes['10']['class_type'], 'VAEDecodeTiled')
        self.assertNotEqual(wf.build('wan22_14b', 'x', 1, 'a'*32)['5']['inputs']['unet_name'],
                            wf.build('wan22_14b', 'x', 1, 'a'*32)['6']['inputs']['unet_name'])

    def test_image_reference_is_preserved_or_explicitly_rejected(self):
        nodes = wf.build('wan22_5b', 'Synthetic fixture', 1, 'a'*32, 'owned-image.png')
        self.assertEqual(nodes['7']['inputs']['start_image'], ['8', 0])
        self.assertEqual(nodes['8']['inputs']['image'], 'owned-image.png')
        with self.assertRaisesRegex(wf.Failure, 'image_conditioning'):
            wf.build('wan22_14b', 'x', 1, 'a'*32, 'owned-image.png')

    def test_missing_models_never_claim_high_quality_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(wf.Failure, 'quality_models_unavailable'):
                wf.verify_models('wan22_5b', Path(directory), Path(directory))
            with self.assertRaisesRegex(wf.Failure, 'not_qualified'):
                wf.active_profile('video.generate', Path(directory), Path(directory))

    def test_stale_qualification_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p = wf.profile('wan22_5b')
            (root/'quality-profile.json').write_text(json.dumps({'workflow_sha256': 'old', 'active':
                {'video.generate': {**p, 'status': 'verified', 'max_segment_seconds': 5}}}))
            with patch.object(wf, 'verify_models') as verify:
                with self.assertRaisesRegex(wf.Failure, 'not_qualified'):
                    wf.active_profile('video.generate', root, root)
                verify.assert_not_called()

    def test_one_and_ten_seconds_are_routed_to_quality(self):
        with patch.object(proxy, 'ENABLED', Mock(is_file=lambda: True)), patch.object(proxy, 'call', return_value={'ok': True, 'job_id': 'd04a'+'a'*28}) as call:
            for seconds in (1, 10, 23):
                out = proxy.route({'operation': 'generate_video', 'owner_context': 'fixture',
                                   'duration_seconds': seconds, 'request_id': 'b'*32, 'prompt': 'x'}, 'a'*64)
                self.assertTrue(out['ok'])
                self.assertEqual(call.call_args.kwargs['input']['duration_seconds'], seconds)
                self.assertFalse(out['retry_submission'])

    def test_quality_offline_never_silently_submits_preview(self):
        with patch.object(proxy, 'ENABLED', Mock(is_file=lambda: True)), patch.object(proxy, 'call', return_value={'ok': False, 'error': {'code': 'high_video_unavailable'}}) as call:
            out = proxy.route({'operation': 'generate_video', 'duration_seconds': 10, 'request_id': 'a'*32, 'prompt': 'x'}, 'a'*64)
            self.assertFalse(out['ok']); self.assertEqual(call.call_count, 1)

    def test_capability_segment_size_is_not_advertised_as_total_duration_limit(self):
        cap = {'ok': True, 'duration_seconds_range': {'minimum': 1, 'mode': 'segmented', 'admission': 'available_disk'}}
        with patch.object(proxy, 'ENABLED', Mock(is_file=lambda: True)), patch.object(proxy, 'call', return_value=cap):
            result = proxy.route({'operation': 'video_capabilities'}, 'a'*64)
            self.assertTrue(result['flexible_duration'])
            self.assertEqual(result['image_to_video_duration_range'], cap['duration_seconds_range'])

    def test_legacy_handles_only_fall_through_get_recovery(self):
        with patch.object(proxy, 'ENABLED', Mock(is_file=lambda: True)), patch.object(proxy, 'call', return_value={'ok': False, 'error': {'code': 'not_found'}}) as call:
            self.assertIsNone(proxy.route({'operation': 'video_job_status', 'job_id': 'd04a'+'b'*28}, 'a'*64))
            self.assertEqual(call.call_args.args[0], 'job')

    def test_disconnect_after_acceptance_keeps_same_handle_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = coord.Engine(directory, 'synthetic')
            job = Path(directory)/'fixture'; job.mkdir()
            with patch.object(engine, 'broker', side_effect=[{'id': 'b'*32}, coord.Failure('quality_worker_offline')]) as broker:
                with self.assertRaisesRegex(coord.Failure, 'worker_timeout_uncertain'):
                    engine.remote_quality('a'*32, 0, 'c'*64, {}, job)
                self.assertEqual([x.args[0] for x in broker.call_args_list], ['POST', 'GET'])
                self.assertEqual(json.loads((job/'remote-0.json').read_text())['job_id'], 'b'*32)
            engine.db.close()


class BrokerLifecycleTests(unittest.TestCase):
    def test_stopped_comfy_runtime_does_not_claim_render_readiness(self):
        with patch.object(quality_broker, 'runtime_ready', return_value=False), patch.object(quality_broker.wf, 'active_profile', return_value={'max_segment_seconds': 5}):
            cap = quality_broker.capabilities()
            self.assertFalse(cap['runtime_ready'])
            self.assertEqual(cap['quality_profiles'], {})
            self.assertFalse(cap['engines']['wangp']['operator_ready'])

    def test_many_sequential_segments_do_not_hit_legacy_daily_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class Broker(quality_broker.QualityLifecycle, legacy_broker.Broker):
                pass
            with patch.object(legacy_broker, 'CONTROL', root), patch.object(quality_broker, 'JOBS', root), patch.object(quality_broker.wf, 'active_profile', return_value={'max_segment_seconds': 5}):
                broker = Broker()
                data = {'kind': 'video.generate', 'prompt': 'private fixture', 'duration_seconds': 5}
                for i in range(12):
                    row = broker.submit('a'*64, f'{i:032x}', data)
                    self.assertEqual(broker.submit('a'*64, f'{i:032x}', data)['id'], row['id'])
                    self.assertEqual(broker.pending.qsize(), 1)
                    broker.pending.get_nowait(); broker.pending.task_done()
                    broker.db.execute("UPDATE jobs SET state='completed' WHERE id=?", (row['id'],)); broker.db.commit()
                broker.db.close()

    def test_expiry_is_owner_ledger_scoped_and_preserves_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class Broker(quality_broker.QualityLifecycle, legacy_broker.Broker):
                pass
            with patch.object(legacy_broker, 'CONTROL', root), patch.object(quality_broker, 'JOBS', root), patch.object(quality_broker.wf, 'active_profile', return_value={'max_segment_seconds': 5}):
                broker = Broker(); data = {'kind': 'video.generate', 'prompt': 'private fixture', 'duration_seconds': 5}
                row = broker.submit('a'*64, 'b'*32, data); broker.pending.get_nowait(); broker.pending.task_done()
                owned = root/row['id']; owned.mkdir(); (owned/'result.mp4').write_bytes(b'fixture')
                unrelated = root/('c'*32); unrelated.mkdir(); (unrelated/'result.mp4').write_bytes(b'preserve')
                broker.db.execute("UPDATE jobs SET state='completed',created=0 WHERE id=?", (row['id'],)); broker.db.commit()
                broker.expire()
                self.assertFalse(owned.exists()); self.assertTrue((unrelated/'result.mp4').exists())
                old = broker.submit('a'*64, 'b'*32, data)
                self.assertEqual(old['error_type'], 'artifact_expired'); self.assertTrue(broker.pending.empty())
                broker.db.close()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg required')
class AssemblyTests(unittest.TestCase):
    def test_restart_does_not_replay_pending_work_or_retain_private_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = coord.Engine(directory, 'synthetic')
            row = engine.submit('a'*64, 'b'*32, {'kind': 'video.generate', 'prompt': 'private fixture', 'duration_seconds': 1})
            job = Path(directory)/row['job_id']; (job/'remote-0.json').write_text(json.dumps({'job_id': 'c'*32}))
            engine.db.close()
            recovered = coord.Engine(directory, 'synthetic')
            self.assertEqual(recovered.row('a'*64, row['job_id'])['state'], 'unknown')
            self.assertFalse((job/'input.json').exists())
            self.assertEqual(json.loads((job/'remote-0.json').read_text())['job_id'], 'c'*32)
            recovered.db.close()

    def test_activation_requires_current_workflow_and_decodable_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jid = 'e'*32; job = root/jid; job.mkdir()
            artifact = job/'result.mp4'
            coord.common.command(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                  'testsrc2=size=1280x720:rate=24', '-t', '1', '-c:v', 'libx264', str(artifact)])
            (job/'input.json').write_text(json.dumps({'kind': 'video.generate'}))
            (job/'comfy-job.json').write_text('{}')
            result = {**wf.profile('wan22_5b'), 'status': 'completed', 'duration_seconds': 1,
                      'workflow_sha256': wf.workflow_hash(), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}
            (job/'result.json').write_text(json.dumps(result))
            with patch.object(qualification, 'JOBS', root), patch.object(qualification, 'FFPROBE', shutil.which('ffprobe')), patch.object(qualification, 'FFMPEG', shutil.which('ffmpeg')), patch.object(qualification.wf, 'verify_models'):
                self.assertEqual(qualification.evidence(jid, 'video.generate', 1)['decoded_frames'], 24)
                with self.assertRaisesRegex(ValueError, 'unqualified_evidence'):
                    qualification.evidence(jid, 'video.animate_image', 1)
                artifact.write_bytes(artifact.read_bytes()+b'changed')
                with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
                    qualification.evidence(jid, 'video.generate', 1)

    def test_native_720p_multisegment_preservation_and_private_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            class Fake(coord.Engine):
                seen = []

                def capabilities(self):
                    p = {'fps': 24, 'max_segment_seconds': 1}
                    return {'ok': True, 'quality_profiles': {'video.generate': p, 'video.animate_image': p}}

                def remote_quality(self, jid, index, owner, payload, job):
                    self.seen.append(payload.copy())
                    f = job/f'source{index}.mp4'
                    coord.common.command(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                          'testsrc2=size=1280x720:rate=24', '-t', '1', '-c:v', 'libx264', str(f)])
                    self.current = f
                    return 'a'*32, {'quality': 'high', 'width': 1280, 'height': 720, 'upscaled': False,
                                    'model': 'fixture', 'sha256': hashlib.sha256(f.read_bytes()).hexdigest()}

                def broker(self, method, path, owner, payload=None, idem=None, dest=None):
                    self.assert_owner = owner
                    if dest is not None:
                        shutil.copyfile(self.current, dest)

            engine = Fake(directory, 'synthetic')
            row = engine.submit('a'*64, 'b'*32, {'kind': 'video.generate', 'prompt': 'PRIVATE FIXTURE', 'duration_seconds': 2})
            engine.run(row['job_id']); result = engine.row('a'*64, row['job_id'])
            self.assertEqual(result['state'], 'completed', result)
            self.assertEqual((result['result']['width'], result['result']['height'], result['result']['frames']), (1280, 720, 48))
            self.assertEqual([x['kind'] for x in engine.seen], ['video.generate', 'video.animate_image'])
            self.assertIn('image_base64', engine.seen[1])
            self.assertEqual(set(p.name for p in (Path(directory)/row['job_id']).iterdir()), {'result.json', 'result.mp4'})
            engine.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.assertNotIn(b'PRIVATE FIXTURE', (Path(directory)/'mechanics.sqlite').read_bytes())
            with self.assertRaises(coord.Failure):
                engine.row('c'*64, row['job_id'])
            engine.db.close()

    def test_preview_worker_result_cannot_be_upscaled_into_a_high_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            class Fake(coord.Engine):
                def capabilities(self):
                    return {'ok': True, 'quality_profiles': {'video.generate': {'fps': 24, 'max_segment_seconds': 1}}}

                def remote_quality(self, *args):
                    return 'a'*32, {'quality': 'preview', 'width': 512, 'height': 288, 'upscaled': False}

            engine = Fake(directory, 'synthetic')
            row = engine.submit('a'*64, 'b'*32, {'kind': 'video.generate', 'prompt': 'fixture', 'duration_seconds': 1})
            engine.run(row['job_id']); result = engine.row('a'*64, row['job_id'])
            self.assertEqual(result['state'], 'failed'); self.assertEqual(result['error_code'], 'quality_profile_mismatch')
            engine.db.close()


if __name__ == '__main__':
    unittest.main()
