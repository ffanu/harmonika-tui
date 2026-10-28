import {CanActivate,ExecutionContext,Injectable,UnauthorizedException,ForbiddenException,Controller,Post,Get,Body,Req,Res,UseGuards} from '@nestjs/common';
import {JwtService} from '@nestjs/jwt';
import {randomBytes,scrypt as scryptCb,timingSafeEqual,createHmac,createHash} from 'node:crypto';
import {promisify} from 'node:util';
import {db,scope} from './db';
const C=require('./core.cjs');const scrypt=promisify(scryptCb);const secret=process.env.JWT_SECRET||'';
if(secret.length<32)throw Error('owner_configuration_required');
export const jwt=new JwtService({secret,signOptions:{expiresIn:3600,issuer:'harmonika-erp',audience:'erp',algorithm:'HS256'}});
const cookie={httpOnly:true,sameSite:'strict' as const,secure:process.env.COOKIE_SECURE!=='false',path:'/',maxAge:3600000};
export async function passwordHash(password:string){const salt=randomBytes(16).toString('hex');return salt+':'+(await scrypt(password,salt,64) as Buffer).toString('hex');}
async function verify(password:string,hash:string){const [salt,want]=hash.split(':');const actual=await scrypt(password,salt||'invalid',64) as Buffer;const expected=Buffer.from(want||'','hex');return actual.length===expected.length&&timingSafeEqual(actual,expected);}
export function csrf(token:string){return createHmac('sha256',secret).update(token).digest('hex');}
export function permit(req:any,...roles:string[]){if(req.auth.role!=='admin'&&!roles.includes(req.auth.role))throw new ForbiddenException('role_forbidden');}
function equal(a:string,b:string){const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);}
@Injectable()
export class AuthGuard implements CanActivate{
 async canActivate(context:ExecutionContext){const req=context.switchToHttp().getRequest();const bearer=String(req.headers.authorization||'').match(/^Bearer (.+)$/);const token=bearer?.[1]||req.cookies?.erp_session;
  let p:any;try{p=await jwt.verifyAsync(token,{issuer:'harmonika-erp',audience:'erp',algorithms:['HS256']});C.requireValue(typeof p.sub==='string'&&typeof p.tid==='string','invalid_token');}catch{throw new UnauthorizedException('login_required');}
  const user=await scope(p.tid,tx=>tx.user.findFirst({where:{id:p.sub,tenantId:p.tid,active:true,deletedAt:null}}));
  if(!user||user.tokenVersion!==p.ver)throw new UnauthorizedException('login_required');req.auth={uid:user.id,tid:user.tenantId,role:user.role};req.token=token;
  if(!['GET','HEAD','OPTIONS'].includes(req.method)&&!bearer){if(req.headers.origin!==process.env.APP_ORIGIN||!equal(String(req.headers['x-csrf-token']||''),csrf(token)))throw new ForbiddenException('csrf_required');}
  return true;
 }
}
const attempts=new Map<string,{count:number,expires:number}>();
@Controller('auth')
export class AuthController{
 @Post('login') async login(@Body()v:any,@Req()req:any,@Res({passthrough:true})res:any){C.keys(v,['tenant','email','password']);C.requireValue(typeof v.tenant==='string'&&/^[a-z0-9-]{1,80}$/.test(v.tenant)&&typeof v.email==='string'&&v.email.length<=120&&typeof v.password==='string'&&v.password.length<=200,'invalid_input');if(req.headers.origin&&req.headers.origin!==process.env.APP_ORIGIN)throw new ForbiddenException('origin_rejected');
  const now=Date.now();for(const [key,value]of attempts)if(value.expires<now)attempts.delete(key);if(attempts.size>10000)throw new ForbiddenException('login_capacity');const key=createHash('sha256').update(String(req.ip)+v.email.toLowerCase()+v.tenant).digest('hex');const attempt=attempts.get(key)||{count:0,expires:now+300000};if(++attempt.count>10)throw new ForbiddenException('login_rate_limited');attempts.set(key,attempt);
  const tenant=await db.tenant.findUnique({where:{slug:v.tenant}});const user=tenant?await scope(tenant.id,tx=>tx.user.findFirst({where:{tenantId:tenant.id,email:v.email.toLowerCase(),active:true,deletedAt:null}})):null;const matched=await verify(v.password,user?.passwordHash||'invalid:');if(!user||!matched)throw new UnauthorizedException('invalid_login');attempts.delete(key);const token=await jwt.signAsync({sub:user.id,tid:user.tenantId,ver:user.tokenVersion});res.cookie('erp_session',token,cookie);return {ok:true,csrf:csrf(token),role:user.role,user_id:user.id};
 }
 @Post('logout') @UseGuards(AuthGuard) async logout(@Req()req:any,@Res({passthrough:true})res:any){await scope(req.auth.tid,tx=>tx.user.updateMany({where:{id:req.auth.uid,tenantId:req.auth.tid},data:{tokenVersion:{increment:1}}}));res.clearCookie('erp_session',{path:'/'});return {ok:true};}
 @Get('me') @UseGuards(AuthGuard) me(@Req()req:any){return {ok:true,role:req.auth.role,csrf:csrf(req.token),user_id:req.auth.uid};}
}
