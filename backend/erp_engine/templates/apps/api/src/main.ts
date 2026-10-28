import 'reflect-metadata';
import {Module,Controller,Get,Catch,ExceptionFilter,ArgumentsHost,HttpException,NestInterceptor,ExecutionContext,CallHandler} from '@nestjs/common';
import {NestFactory} from '@nestjs/core';
import {map} from 'rxjs/operators';
import cookieParser from 'cookie-parser';
import express from 'express';
import {AuthController,AuthGuard} from './auth';
import {DataController} from './data';
import {ExtensionController} from './extensions';
import {CommercialController} from './commercial';
import {ProcurementController} from './procurement';
import {SourcingController} from './sourcing';
import {IspController} from './isp';
import {ManufacturingController} from './manufacturing';
import {PosController} from './pos';
import {CrmController} from './crm';
import {ProjectController} from './projects';
import {ProjectControlsController} from './project-controls';
import {WorkBillingController} from './work-billing';
import {db} from './db';
const C=require('./core.cjs');
@Controller() class HealthController{@Get('health') health(){return {ok:true};}}
@Module({controllers:[AuthController,DataController,ExtensionController,CommercialController,ProcurementController,SourcingController,IspController,ManufacturingController,PosController,CrmController,ProjectController,ProjectControlsController,WorkBillingController,HealthController],providers:[AuthGuard]})class App{}
@Catch()class Errors implements ExceptionFilter{catch(e:any,host:ArgumentsHost){const validation=C.validationCode(e);let code=e instanceof HttpException?e.message:validation||'operation_failed';let status=e instanceof HttpException?e.getStatus():['P2002','P2034'].includes(e?.code)?409:validation?422:500;if(C.databaseConflict(e)){code='transaction_conflict';status=409;}if(e?.code==='P2002')code='record_conflict';host.switchToHttp().getResponse().status(status).json({ok:false,error:{code}});}}
class Json implements NestInterceptor{intercept(context:ExecutionContext,next:CallHandler){return next.handle().pipe(map((v:any)=>C.json(v)));}}
async function main(){if(!process.env.APP_ORIGIN||!process.env.DATABASE_URL)throw Error('owner_configuration_required');const app=await NestFactory.create(App,{logger:false,bodyParser:false});app.use(express.json({limit:'64kb'}));app.use(cookieParser());app.use((req:any,res:any,next:any)=>{res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');next();});app.useGlobalFilters(new Errors());app.useGlobalInterceptors(new Json());app.enableShutdownHooks();await db.$connect();await app.listen(Number(process.env.PORT||4000),process.env.LISTEN_HOST||'0.0.0.0');}
main().catch(()=>{console.error('ERP startup failed; verify owner configuration.');process.exitCode=1;});
