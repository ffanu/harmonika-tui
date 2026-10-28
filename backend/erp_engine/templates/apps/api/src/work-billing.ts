import {Controller,Get,Post,Req,Body,Param,Query,UseGuards,ConflictException,NotFoundException} from '@nestjs/common';
import {AuthGuard,permit} from './auth';
import {scope} from './db';
import {change,audit,uuid,pagination} from './data';
import {projectAccess,touchProject} from './projects';
import {ensureOpen} from './accounting';
const C=require('./core.cjs');

function input(v:any,final=false){
 C.keys(v,final?['revision','issuedOn','taxBps','lines','previewHash','confirmed']:['revision','issuedOn','taxBps','lines']);
 C.integer(v.revision,0,2147483647);C.integer(v.taxBps,0,10000);
 const day=C.calendarDate(v.issuedOn);C.requireValue(day.toISOString().slice(0,10)<=new Date().toISOString().slice(0,10),'future_invoice_date');
 C.requireValue(Array.isArray(v.lines)&&v.lines.length>0&&v.lines.length<=100,'invalid_lines');
 const seen=new Set();for(const line of v.lines){C.keys(line,['entryId','rateMinorPerHour']);uuid(line.entryId);C.requireValue(!seen.has(line.entryId),'duplicate_work_entry');seen.add(line.entryId);C.requireValue(C.minor(line.rateMinorPerHour)>0n,'positive_rate_required');}
 if(final)C.requireValue(v.confirmed===true&&typeof v.previewHash==='string'&&/^[a-f0-9]{64}$/.test(v.previewHash),'billing_preview_required');
 return day;
}
async function quote(tx:any,r:any,id:string,v:any){
 const project=await projectAccess(tx,r,id);
 if(!['ACTIVE','COMPLETED'].includes(project.status))throw new ConflictException('project_not_billable');
 if(project.revision!==v.revision)throw new ConflictException('revision_conflict');
 if(!project.customerId||!await tx.customer.findFirst({where:{id:project.customerId,tenantId:r.auth.tid,deletedAt:null}}))throw new NotFoundException('project_customer_required');
 const selected=await tx.workTimeEntry.findMany({where:{tenantId:r.auth.tid,projectId:id,id:{in:v.lines.map((l:any)=>l.entryId)},voidedAt:null,deletedAt:null,billingLine:null},include:{task:{select:{title:true}}}});
 if(selected.length!==v.lines.length)throw new ConflictException('work_entries_not_billable');
 const byId=new Map<string,any>(selected.map((e:any)=>[e.id,e]));
 const rows=v.lines.map((l:any)=>({entryId:l.entryId,durationSeconds:byId.get(l.entryId).durationSeconds,rateMinorPerHour:l.rateMinorPerHour,taskTitle:byId.get(l.entryId).task.title})).sort((a:any,b:any)=>a.entryId.localeCompare(b.entryId));
 const result=C.workInvoice(rows,v.taxBps);
 const snapshot=C.json({projectId:id,customerId:project.customerId,revision:project.revision,issuedOn:v.issuedOn,taxBps:v.taxBps,...result});
 return {project,snapshot,previewHash:C.hash(snapshot),result};
}
@Controller('work') @UseGuards(AuthGuard)
export class WorkBillingController{
 @Get('projects/:id/bills') bills(@Req()r:any,@Param('id')id:string,@Query('page')p:any,@Query('limit')limit:any){permit(r,'operator','finance');uuid(id);return scope(r.auth.tid,async tx=>{const project=await projectAccess(tx,r,id);if(!['admin','finance'].includes(r.auth.role)&&project.ownerId!==r.auth.uid)throw new NotFoundException('project_not_found');return tx.workBill.findMany({where:{tenantId:r.auth.tid,projectId:id},include:{lines:true,invoice:{select:{id:true,status:true,totalMinor:true,taxMinor:true,subtotalMinor:true}}},orderBy:[{createdAt:'desc'},{id:'asc'}],...pagination(p,limit)});});}
 @Post('projects/:id/billing-preview') preview(@Req()r:any,@Param('id')id:string,@Body()v:any){permit(r,'finance');uuid(id);input(v);return scope(r.auth.tid,async tx=>{await ensureOpen(tx,r.auth.tid,v.issuedOn.slice(0,7));const q=await quote(tx,r,id,v);return {...q.snapshot,previewHash:q.previewHash,invoice_created:false,rounding:'per_entry_half_up_seconds_over_3600'};});}
 @Post('projects/:id/bills') issue(@Req()r:any,@Param('id')id:string,@Body()v:any){permit(r,'finance');uuid(id);const day=input(v,true);return change(r,'work.bill:'+id,v,async tx=>{
  await ensureOpen(tx,r.auth.tid,v.issuedOn.slice(0,7));const q=await quote(tx,r,id,v);
  if(q.previewHash!==v.previewHash)throw new ConflictException('billing_preview_stale');
  await touchProject(tx,r,q.project);
  const invoice=await tx.invoice.create({data:{tenantId:r.auth.tid,customerId:q.project.customerId,status:'ISSUED',subtotalMinor:q.result.subtotal,taxMinor:q.result.tax,totalMinor:q.result.total}});
  const bill=await tx.workBill.create({data:{tenantId:r.auth.tid,projectId:id,invoiceId:invoice.id,issuedOn:day,taxBps:v.taxBps}});
  await tx.workBillLine.createMany({data:q.result.rows.map((l:any)=>({tenantId:r.auth.tid,projectId:id,billId:bill.id,entryId:l.entryId,durationSeconds:l.durationSeconds,rateMinorPerHour:BigInt(l.rateMinorPerHour),amountMinor:l.amountMinor,taskTitle:l.taskTitle}))});
  await audit(tx,r,'work.invoice_issued',bill.id);
  return {id:bill.id,invoiceId:invoice.id,projectId:id,totalMinor:invoice.totalMinor,issuedOn:v.issuedOn,revision:v.revision+1,ledger_posted:false,payment_verified:false};
 },tx=>projectAccess(tx,r,id));}
}
