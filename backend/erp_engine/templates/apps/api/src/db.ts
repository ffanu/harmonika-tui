import {PrismaClient,Prisma} from '@prisma/client';
import {PrismaPg} from '@prisma/adapter-pg';
export const db=new PrismaClient({adapter:new PrismaPg({connectionString:process.env.DATABASE_URL}),log:[]});
export async function scope<T>(tenantId:string,fn:(tx:Prisma.TransactionClient)=>Promise<T>){return db.$transaction(async tx=>{await tx.$executeRaw`SELECT set_config('app.tenant_id',${tenantId},true)`;return fn(tx)},{isolationLevel:Prisma.TransactionIsolationLevel.Serializable,timeout:10000});}
