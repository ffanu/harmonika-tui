'use strict';
const crypto=require('node:crypto');
class ValidationError extends Error{constructor(code){super(code);this.name='HarmonikaValidationError';}}
function requireValue(ok,code){if(!ok)throw new ValidationError(code);}
function keys(v,allowed){requireValue(v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).every(k=>allowed.includes(k)),'invalid_input');}
function minor(v){requireValue(typeof v==='string'&&/^(0|[1-9][0-9]{0,17})$/.test(v),'invalid_money');return BigInt(v);}
function integer(v,min=1,max=1000000){requireValue(Number.isSafeInteger(v)&&v>=min&&v<=max,'invalid_integer');return v;}
function journal(lines){requireValue(Array.isArray(lines)&&lines.length>=2&&lines.length<=100,'invalid_journal');let debit=0n,credit=0n;
 for(const l of lines){keys(l,['accountCode','debitMinor','creditMinor']);requireValue(typeof l.accountCode==='string'&&/^[A-Za-z0-9_-]{1,20}$/.test(l.accountCode),'invalid_account');const d=minor(l.debitMinor),c=minor(l.creditMinor);requireValue((d===0n)!==(c===0n),'invalid_journal_line');debit+=d;credit+=c;}
 requireValue(debit>0n&&debit===credit,'unbalanced_journal');return {debit,credit};}
function invoice(lines,taxBps){requireValue(Array.isArray(lines)&&lines.length>=1&&lines.length<=100,'invalid_lines');integer(taxBps,0,10000);const subtotal=lines.reduce((n,l)=>n+minor(l.unitMinor)*BigInt(integer(l.quantity)),0n);const tax=(subtotal*BigInt(taxBps)+5000n)/10000n;requireValue(subtotal+tax<=9223372036854775807n,'amount_exceeds_storage_limit');return {subtotal,tax,total:subtotal+tax};}
function workInvoice(lines,taxBps){requireValue(Array.isArray(lines)&&lines.length>=1&&lines.length<=100,'invalid_lines');integer(taxBps,0,10000);const seen=new Set();const rows=lines.map(l=>{requireValue(typeof l.entryId==='string'&&!seen.has(l.entryId),'duplicate_work_entry');seen.add(l.entryId);integer(l.durationSeconds,1,86400);const rate=minor(l.rateMinorPerHour);requireValue(rate>0n,'positive_rate_required');return {...l,amountMinor:(BigInt(l.durationSeconds)*rate+1800n)/3600n};});const subtotal=rows.reduce((n,l)=>n+l.amountMinor,0n),tax=(subtotal*BigInt(taxBps)+5000n)/10000n;requireValue(subtotal>0n,'positive_invoice_required');requireValue(subtotal+tax<=9223372036854775807n,'amount_exceeds_storage_limit');return {rows,subtotal,tax,total:subtotal+tax};}
function canonical(v){if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';return JSON.stringify(v);}
function hash(v){return crypto.createHash('sha256').update(canonical(v)).digest('hex');}
function json(v){return JSON.parse(JSON.stringify(v,(_,x)=>typeof x==='bigint'?x.toString():x));}
function databaseConflict(error){
 const codes=[error?.code,error?.meta?.code,error?.cause?.code,error?.meta?.driverAdapterError?.cause?.code];
 return codes.some(code=>['P2034','40001','40P01'].includes(code)) || error?.name==='DriverAdapterError'&&error?.cause?.kind==='TransactionWriteConflict';
}
function validationCode(error){return error instanceof ValidationError&&typeof error.message==='string'&&/^[a-z_]{1,60}$/.test(error.message)?error.message:null;}
function calendarDate(value){requireValue(typeof value==='string'&&/^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}$/.test(value),'invalid_date');const [y,m,d]=value.split('-').map(Number),date=new Date(Date.UTC(y,m-1,d));requireValue(date.getUTCFullYear()===y&&date.getUTCMonth()===m-1&&date.getUTCDate()===d,'invalid_date');return date;}
function instant(value){requireValue(typeof value==='string'&&/^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,3})?(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(value),'timezone_timestamp_required');calendarDate(value.slice(0,10));const h=Number(value.slice(11,13)),m=Number(value.slice(14,16)),sec=Number(value.slice(17,19));requireValue(h<24&&m<60&&sec<60,'invalid_timestamp');if(!value.endsWith('Z')){const zone=value.slice(-6),zh=Number(zone.slice(1,3)),zm=Number(zone.slice(4));requireValue(zh<=14&&zm<60&&(zh!==14||zm===0),'invalid_timezone');}const date=new Date(value);requireValue(Number.isFinite(date.getTime()),'invalid_timestamp');return date;}
module.exports={requireValue,keys,minor,integer,journal,invoice,canonical,hash,json,databaseConflict,validationCode,calendarDate,instant,workInvoice};
