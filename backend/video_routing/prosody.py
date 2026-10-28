"""Phrase-delivery preset, not a claim of native neural emotion control."""
import base64,hashlib,io,re,wave

def phrases(text):
 # Preserve every original word and punctuation; no factual claims are added.
 chunks=[x for x in re.split(r'(?<=[.!?])\s+',text.strip()) if x]
 return chunks[:2]+[' '.join(chunks[2:])] if len(chunks)>3 else chunks

def promotional(text,language,voice,speed,call):
 chunks=phrases(text)
 if not chunks or len(text.encode())>600:raise ValueError('promotional_text_too_long')
 frames=[];fmt=None;meta=None;pause_seconds=.16
 for index,part in enumerate(chunks):
  data=call('/speech',{'text':part,'language':language,'voice':voice,'speed':speed})
  raw=base64.b64decode(data['audio_base64'],validate=True)
  if len(raw)!=data['bytes'] or hashlib.sha256(raw).hexdigest()!=data['sha256']:raise ValueError('invalid_audio_artifact')
  with wave.open(io.BytesIO(raw)) as reader:
   current=(reader.getnchannels(),reader.getsampwidth(),reader.getframerate())
   if current!=(1,2,44100) or (fmt is not None and current!=fmt):raise ValueError('invalid_audio_artifact')
   fmt=current;payload=reader.readframes(reader.getnframes());frames.append(payload)
  if index<len(chunks)-1:frames.append(b'\x00'*int(fmt[2]*pause_seconds)*fmt[0]*fmt[1])
  meta=data
 payload=b''.join(frames);duration=len(payload)/(fmt[0]*fmt[1]*fmt[2])
 if not 0<duration<=55 or len(payload)>5*1024*1024:raise ValueError('audio_output_limit')
 output=io.BytesIO()
 with wave.open(output,'wb') as writer:
  writer.setnchannels(fmt[0]);writer.setsampwidth(fmt[1]);writer.setframerate(fmt[2]);writer.writeframes(payload)
 raw=output.getvalue()
 return {**meta,'audio_base64':base64.b64encode(raw).decode(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'duration_seconds':round(duration,3),'style':'promosi_ceria','style_implementation':'phrase_delivery_and_copy','native_emotion_control':False,'phrase_count':len(chunks)}
