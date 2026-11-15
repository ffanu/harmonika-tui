// The server transport adapter selects error codes only. Never retain a body/message.
const STOP = new Map([
  ['tool_secret_reference_required','credential_reference'],
  ['invalid_tool_arguments','invalid_arguments'],['invalid_tool_response','invalid_arguments'],
  ['invalid_tools','invalid_arguments'],['invalid_tool_history','invalid_arguments'],
  ['invalid_request_error','invalid_arguments'],['tool_choice_not_satisfied','invalid_arguments'],
  ['insufficient_quota','quota'],['quota_exceeded','quota'],['entitlement_denied','access'],
  ['license_revoked','access'],['invalid_api_key','access'],['authentication_error','access'],
]);
const TRANSIENT = new Set(['tool_engine_unavailable','tool_engine_busy','backend_unavailable','service_unavailable','rate_limit_exceeded']);
const TIMEOUT = new Set(['tool_engine_timeout','request_timeout','ETIMEDOUT']);
const TYPES = new Set(['provider.auth','provider.invalid-request','provider.quota','provider.rate-limit','provider.internal','provider.network','provider.timeout','provider.transport']);
function mechanics(error) {
  const status=Number.isInteger(error?.status)&&error.status>=100&&error.status<=599?error.status:null;
  const type=TYPES.has(error?.type)?error.type:'unknown';
  let code=STOP.has(error?.type)||TRANSIENT.has(error?.type)||TIMEOUT.has(error?.type)?error.type:null;
  if(!code && status>=400) {
    const body=error?.response?.body;
    if(typeof body==='string' && body.length<=16384) {
      try {
        const value=JSON.parse(body), candidate=value?.error?.code;
        if(typeof candidate==='string' && (STOP.has(candidate)||TRANSIENT.has(candidate)||TIMEOUT.has(candidate))) code=candidate;
      } catch {}
    }
  }
  return {status,type,code};
}
function decideRetry(error,attempt,decision,partial=false) {
  const m=mechanics(error);
  const result=(retry,reason)=>({retry,reason,code:m.code??m.type,status:m.status,attempt:Number.isInteger(attempt)&&attempt>=1&&attempt<=100?attempt:0});
  if(STOP.has(m.code)) return result(false,STOP.get(m.code));
  if(m.status===401||m.status===403||m.type==='provider.auth')return result(false,'access');
  if(m.status===402||m.type==='provider.quota')return result(false,'quota');
  if((m.status>=400&&m.status<500&&m.status!==408&&m.status!==429)||m.type==='provider.invalid-request')return result(false,'invalid_arguments');
  if(partial)return result(false,'partial_response');
  if(decision?.retry!==true)return result(false,'native_stopped');
  if(!Number.isInteger(attempt)||attempt<2||attempt>3)return result(false,'retry_budget');
  if((TIMEOUT.has(m.code)||m.status===408||m.status===504||m.type==='provider.timeout')&&attempt>2)return result(false,'timeout_budget');
  // A plugin may reduce retry, never turn a native denial into retry or replay work.
  const transient=TRANSIENT.has(m.code)||TIMEOUT.has(m.code)||m.status===408||m.status===429||m.status>=500||['provider.network','provider.transport','provider.timeout'].includes(m.type);
  return result(transient,transient?'temporary':'unknown_error');
}
function retryAdvice(reason) {
  return ({credential_reference:'Gunakan referensi credential privat atau environment; jangan tempel password/API key.',invalid_arguments:'Perbaiki argumen atau schema tool sebelum melanjutkan.',access:'Periksa akses atau lisensi. Request tidak dikirim ulang.',quota:'Periksa kuota layanan; tidak mengganti provider.',partial_response:'Respons sudah mulai diterima. Periksa hasil sebelum melanjutkan.',retry_budget:'Batas retry tercapai. Periksa koneksi dan status pekerjaan.',timeout_budget:'Timeout berulang. Perkecil langkah dan periksa hasil sebelumnya.',temporary:'Gangguan sementara; retry native dibatasi.',native_stopped:'Native menghentikan retry.',unknown_error:'Error belum dikenali; periksa status sebelum mencoba lagi.'})[reason]??'Periksa status pekerjaan.';
}
export {decideRetry,retryAdvice,mechanics};
