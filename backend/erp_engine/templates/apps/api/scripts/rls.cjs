'use strict';
const fs=require('node:fs'),path=require('node:path');const {db}=require('../dist/db.js');
(async()=>{const sql=fs.readFileSync(path.join(__dirname,'../prisma/tenant-policies.sql'),'utf8');for(const statement of sql.replace(/^--.*$/mg,'').split(';').map(s=>s.trim()).filter(Boolean))await db.$executeRawUnsafe(statement);console.log('Tenant policies installed. Runtime role must not be superuser or BYPASSRLS.');})().catch(()=>{console.error('Tenant policy installation failed.');process.exitCode=1;}).finally(()=>db.$disconnect());
