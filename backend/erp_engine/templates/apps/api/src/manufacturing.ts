import {Controller,Get,Post,Req,Body,Param,Query,UseGuards,ConflictException,NotFoundException} from '@nestjs/common';
import {AuthGuard,permit} from './auth';import {scope} from './db';import {pagination,change,audit,text,uuid} from './data';
const C=require('./core.cjs');
/** Validate all stored immutable BOM versions together; no recursion or unbounded scans. */
export async function validateBomGraph(tx:any,tenantId:string,output:string,components:any[]){
 const old=await tx.billOfMaterial.findMany({where:{tenantId,deletedAt:null},include:{components:true},take:501});
 if(old.length>=500)throw new ConflictException('bom_validation_capacity');
 const graph=new Map<string,Set<string>>();let edges=0;
 for(const bom of [...old,{outputProductId:output,components}]){
  const children=graph.get(bom.outputProductId)||new Set<string>();graph.set(bom.outputProductId,children);
  for(const line of bom.components){if(!children.has(line.productId)){children.add(line.productId);edges++;}}
 }
 if(edges>10000)throw new ConflictException('bom_validation_capacity');
 const incoming=new Map<string,number>();
 for(const [parent,children] of graph){if(!incoming.has(parent))incoming.set(parent,0);for(const child of children)incoming.set(child,(incoming.get(child)||0)+1);}
 const ready=[...incoming.keys()].filter(k=>incoming.get(k)===0);let visited=0;
 while(ready.length){const n=ready.pop()!;visited++;for(const child of graph.get(n)||[]){const value=incoming.get(child)!-1;incoming.set(child,value);if(value===0)ready.push(child);}}
 if(visited!==incoming.size)throw new ConflictException('bom_cycle');
}
@Controller('manufacturing') @UseGuards(AuthGuard)
export class ManufacturingController{
 @Get('boms') boms(@Req()r:any,@Query('page')p:any,@Query('limit')limit:any){permit(r,'operator','finance');return scope(r.auth.tid,tx=>tx.billOfMaterial.findMany({where:{tenantId:r.auth.tid,deletedAt:null},include:{components:true,outputProduct:{select:{name:true,sku:true}}},orderBy:[{createdAt:'desc'},{id:'asc'}],...pagination(p,limit)}));}
 @Post('boms') createBom(@Req()r:any,@Body()v:any){
  permit(r,'operator');C.keys(v,['outputProductId','version','components']);uuid(v.outputProductId);C.integer(v.version,1,2147483647);
  C.requireValue(Array.isArray(v.components)&&v.components.length>0&&v.components.length<=100,'invalid_components');const ids=new Set<string>();
  for(const line of v.components){C.keys(line,['productId','quantity','unitCostMinor']);uuid(line.productId);C.integer(line.quantity);C.minor(line.unitCostMinor);C.requireValue(!ids.has(line.productId),'duplicate_component');ids.add(line.productId);}
  return change(r,'manufacturing.bom',v,async tx=>{
   for(const id of [v.outputProductId,...ids])if(!await tx.product.findFirst({where:{id,tenantId:r.auth.tid,deletedAt:null}}))throw new NotFoundException('product_not_found');
   await validateBomGraph(tx,r.auth.tid,v.outputProductId,v.components);
   const bom=await tx.billOfMaterial.create({data:{tenantId:r.auth.tid,outputProductId:v.outputProductId,version:v.version}});
   await tx.bomComponent.createMany({data:v.components.map((line:any)=>({tenantId:r.auth.tid,bomId:bom.id,productId:line.productId,quantity:line.quantity,unitCostMinor:C.minor(line.unitCostMinor)}))});
   await audit(tx,r,'manufacturing.bom_created',bom.id);return bom;
  });
 }
 @Get('orders') orders(@Req()r:any,@Query('page')p:any,@Query('limit')limit:any){permit(r,'operator','finance');return scope(r.auth.tid,tx=>tx.productionOrder.findMany({where:{tenantId:r.auth.tid,deletedAt:null},include:{inputs:true,outputProduct:{select:{name:true,sku:true}}},orderBy:[{createdAt:'desc'},{id:'asc'}],...pagination(p,limit)}));}
 @Post('orders') createOrder(@Req()r:any,@Body()v:any){
  permit(r,'operator');C.keys(v,['bomId','quantity']);uuid(v.bomId);C.integer(v.quantity);
  return change(r,'manufacturing.order',v,async tx=>{
   const bom=await tx.billOfMaterial.findFirst({where:{id:v.bomId,tenantId:r.auth.tid,deletedAt:null},include:{components:true}});if(!bom)throw new NotFoundException('bom_not_found');
   let cost=0n;const inputs=[];
   for(const component of bom.components){const quantity=component.quantity*v.quantity;C.integer(quantity,1,1000000000);cost+=BigInt(quantity)*component.unitCostMinor;inputs.push({tenantId:r.auth.tid,productId:component.productId,quantity,unitCostMinor:component.unitCostMinor});}
   C.requireValue(cost<=9223372036854775807n,'amount_exceeds_storage_limit');
   const order=await tx.productionOrder.create({data:{tenantId:r.auth.tid,bomId:bom.id,outputProductId:bom.outputProductId,quantity:v.quantity,materialEstimateMinor:cost}});
   await tx.productionInput.createMany({data:inputs.map(line=>({...line,orderId:order.id}))});await audit(tx,r,'manufacturing.order_created',order.id);
   return {...order,stock_reserved:false,ledger_posted:false};
  });
 }
 @Post('orders/:id/quality') quality(@Req()r:any,@Param('id')id:string,@Body()v:any){
  permit(r,'operator');uuid(id);C.keys(v,['revision','result','note','confirmed']);C.integer(v.revision,0,2147483647);C.requireValue(['PASSED','FAILED'].includes(v.result)&&v.confirmed===true,'quality_confirmation_required');text(v.note,1000);
  return change(r,'manufacturing.quality:'+id,v,async tx=>{const changed=await tx.productionOrder.updateMany({where:{id,tenantId:r.auth.tid,deletedAt:null,revision:v.revision,status:'RELEASED'},data:{qualityStatus:v.result,qualityNote:v.note,qualityBy:r.auth.uid,qualityAt:new Date(),revision:{increment:1}}});if(changed.count!==1)throw new ConflictException('quality_state_or_revision_conflict');await audit(tx,r,'manufacturing.quality_recorded',id);return {id,qualityStatus:v.result,revision:v.revision+1};});
 }
 @Post('orders/:id/:action') action(@Req()r:any,@Param('id')id:string,@Param('action')action:string,@Body()v:any){
  permit(r,'operator');uuid(id);C.keys(v,['revision','confirmed']);C.integer(v.revision,0,2147483647);C.requireValue(v.confirmed===true&&['release','complete','cancel'].includes(action),'invalid_action');
  return change(r,'manufacturing.'+action+':'+id,v,async tx=>{
   const order=await tx.productionOrder.findFirst({where:{id,tenantId:r.auth.tid,deletedAt:null},include:{inputs:true}});if(!order)throw new NotFoundException('production_order_not_found');if(order.revision!==v.revision)throw new ConflictException('revision_conflict');
   if(action==='release'&&order.status!=='DRAFT'||action==='complete'&&order.status!=='RELEASED'||action==='cancel'&&!['DRAFT','RELEASED'].includes(order.status))throw new ConflictException('invalid_production_state');
   if(action==='complete'){
    if(order.qualityStatus!=='PASSED')throw new ConflictException('quality_pass_required');
    const moves:Array<{productId:string;delta:number}>=order.inputs.map((line:any)=>({productId:line.productId,delta:-line.quantity}));moves.push({productId:order.outputProductId,delta:order.quantity});moves.sort((a,b)=>a.productId.localeCompare(b.productId));
    for(const move of moves){const updated=await tx.product.updateMany({where:{id:move.productId,tenantId:r.auth.tid,deletedAt:null,...(move.delta<0?{quantity:{gte:-move.delta}}:{quantity:{lte:1000000000-move.delta}})},data:{quantity:{increment:move.delta},revision:{increment:1}}});if(updated.count!==1)throw new ConflictException('production_stock_unavailable');await tx.stockMovement.create({data:{tenantId:r.auth.tid,productId:move.productId,delta:move.delta,reason:'production:'+id}});}
   }
   const status=action==='release'?'RELEASED':action==='complete'?'COMPLETED':'CANCELLED';const updated=await tx.productionOrder.updateMany({where:{id,tenantId:r.auth.tid,revision:v.revision,status:order.status},data:{status,revision:{increment:1},...(status==='COMPLETED'?{completedAt:new Date()}:{})}});if(updated.count!==1)throw new ConflictException('revision_conflict');await audit(tx,r,'manufacturing.'+action,id);return {id,status,revision:v.revision+1,ledger_posted:false};
  });
 }
}
