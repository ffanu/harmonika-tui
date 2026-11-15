import {Plugin} from '@opencode/plugin/tui'
import {For, Show, createMemo, createSignal} from 'solid-js'
import {createHash, randomUUID} from 'node:crypto'
import {fileURLToPath} from 'node:url'
import {realpathSync} from 'node:fs'
import {createMonitor, classifyConnection, backoff, actionRows, labels, text, phaseLabels} from './model.mjs'
import {retryAdvice} from './retry.mjs'
import {RPC} from './runtime.mjs'
const helper = fileURLToPath(new URL('./helper/tui_control.py', import.meta.url))
const names = {engine:'Engine',jobs:'Pekerjaan',memory:'Memori proyek',connection:'Koneksi'} as const
type Tab = keyof typeof names
type Result = Record<string, any>

export default Plugin.define({
  id:'harmonika.control',
  setup(context) {
    const monitor = createMonitor()
    const [revision, setRevision] = createSignal(0)
    const [tab, setTab] = createSignal<Tab>('engine')
    const [health, setHealth] = createSignal<Result>({backend:{status:'not_checked'},graphics:{status:'not_checked'}})
    const [jobs, setJobs] = createSignal<Result[]>([])
    const [notes, setNotes] = createSignal<Result[]>([])
    const [message, setMessage] = createSignal('')
    const [busy, setBusy] = createSignal(false)
    const [pending, setPending] = createSignal<Result>()
    const [runtime, setRuntime] = createSignal<Result>()
    const [width, setWidth] = createSignal(context.renderer.width)
    let stopped = false, probing = false, failures = 0, blockedProbe = false
    let previous: any = {type:'home'}
    let latestProbe = 0
    let lastProject = ''
    let runtimeProbing=false
    const processes = new Set<ReturnType<typeof Bun.spawn>>()
    const sessionID = () => {
      const route=context.ui.router.current()
      return route.type==='session'?route.sessionID:context.ui.panel.current()?.sessionID ?? (route.type==='plugin'&&route.name==='harmonika-control'&&previous.type==='session'?previous.sessionID:undefined)
    }
    const project = () => {
      const id=sessionID()
      const directory=(id?context.data.session.get(id)?.location.directory:undefined) ?? context.location?.directory ?? context.data.location.default().directory
      let canonical=directory
      try{canonical=realpathSync(directory)}catch{}
      return 'tui-'+createHash('sha256').update(canonical).digest('hex').slice(0,40)
    }
    const execution = createMemo(() => {
      revision()
      const id=sessionID(), snapshot=runtime()
      const local=monitor.get(id,id?context.data.session.status(id)==='running':false)
      return snapshot&&snapshot.session_id===id&&snapshot.observed&&(!local.observed||snapshot.updated_at>=local.updated_at)?snapshot:local
    })
    const workflow=()=>runtime()?.session_id===sessionID()?runtime():undefined
    async function refreshRuntime() {
      const id=sessionID()
      if(!id||runtimeProbing||stopped||!opened())return
      runtimeProbing=true
      try {
        const value:any=await context.client.rpc(RPC).get({sessionID:id},{location:context.data.session.get(id)?.location??context.location,signal:AbortSignal.timeout(2500)})
        if(!stopped&&id===sessionID())setRuntime({...value,session_id:id})
      } catch {if(!stopped&&id===sessionID())setRuntime(undefined)}
      finally {runtimeProbing=false}
    }
    const usage=()=>{
      revision()
      const id=sessionID(),session=id?context.data.session.get(id):undefined
      const tokens=session?.tokens
      return {input:tokens?.input,output:tokens?.output,cost:session?.cost}
    }
    const tone = (state:string) => state==='online'||state==='completed'?context.theme.text.feedback.success.base:state==='failed'||state==='auth_required'?context.theme.text.feedback.error.base:context.theme.text.muted
    const notify = (value:string, kind:'info'|'success'|'error'='info') => context.ui.toast.show({title:'Harmonika',message:value,variant:kind})
    async function call(payload:Result):Promise<Result> {
      if(stopped) return {ok:false,error:'control_closed'}
      let child:ReturnType<typeof Bun.spawn>|undefined,timer:ReturnType<typeof setTimeout>|undefined
      const failure=()=>({ok:false,error:payload.action==='memory'&&['set','clear'].includes(payload.operation)?'submission_unknown':'control_unavailable'})
      try {
        child=Bun.spawn([String(context.options.python ?? (process.platform==='win32'?'python':'python3')),helper],{stdin:'pipe',stdout:'pipe',stderr:'ignore'})
        processes.add(child)
        timer=setTimeout(()=>child?.kill(),payload.action==='memory'?12000:8000)
        child.stdin.write(JSON.stringify(payload));child.stdin.end()
        const result=await new Response(child.stdout).text()
        await child.exited
        if(stopped) return {ok:false,error:'control_closed'}
        if(result.length>262144) return failure()
        const value=JSON.parse(result)
        return value&&typeof value==='object'&&!Array.isArray(value)?value:failure()
      } catch { return failure() }
      finally {if(timer)clearTimeout(timer);if(child)processes.delete(child)}
    }
    function clearProject() {
      const value=project()
      if(lastProject!==value){lastProject=value;setJobs([]);setNotes([]);setMessage('');setRuntime(undefined)}
      return value
    }
    async function refresh(force=false) {
      if(probing||stopped) return
      if(!force&&blockedProbe) return
      if(!force && Date.now()-latestProbe<backoff(failures)) return
      probing=true;latestProbe=Date.now()
      const pid=clearProject()
      try {
        const state=await call({action:'status'})
        if(stopped) return
        if(state.backend) setHealth(state)
        else setHealth({backend:{status:state.error ?? 'offline'},graphics:{status:'unknown'},checked_at:Date.now()/1000})
        failures=state.backend?.status==='online'?0:failures+1
        blockedProbe=['auth_required','quota_exceeded','rate_limit','key_not_configured','unsafe_key_file','invalid_key_file'].includes(state.backend?.status ?? state.error)
        // Poll existing jobs only while their panel is open; never submit work here.
        if(tab()==='jobs' && opened()) {
          const rows=await call({action:'jobs',project_id:pid})
          if(!stopped&&project()===pid&&rows.ok) setJobs(rows.jobs ?? [])
        }
      } finally {probing=false}
    }
    function opened() {
      const route=context.ui.router.current()
      return route.type==='plugin'&&route.name==='harmonika-control' || context.ui.panel.current()?.name==='harmonika-control'
    }
    function open(next:Tab) {
      clearProject();setTab(next)
      const route=context.ui.router.current()
      if(route.type!=='plugin'||route.name!=='harmonika-control') previous={...route}
      context.ui.dialog.clear()
      if(!context.ui.panel.open('harmonika-control')) context.ui.router.navigate({type:'plugin',name:'harmonika-control'})
      void refresh(true)
      void refreshRuntime()
    }
    function close() {
      context.ui.panel.close()
      if(context.ui.router.current().type==='plugin') context.ui.router.navigate(previous)
    }
    async function stopSession() {
      const id=sessionID()
      if(!id||context.data.session.status(id)!=='running') return notify('Tidak ada respons sesi yang sedang berjalan.')
      try {await context.client.session.interrupt({sessionID:id});notify('Permintaan Stop dikirim. Status menunggu konfirmasi engine.')}
      catch {notify('Stop belum terkonfirmasi. Periksa status sebelum mencoba tindakan berikutnya.','error')}
    }
    async function reconnect() {
      await refresh(true)
      const id=sessionID()
      if(id) {
        try {await context.data.session.sync(id);notify('Metadata sesi diperbarui. Tidak ada tugas yang dikirim ulang.','success')}
        catch {notify('Koneksi sesi belum pulih. Tugas tidak dikirim ulang.','error')}
      }
    }
    async function auditConfig() {
      setBusy(true)
      try {
        const result=await call({action:'audit_config'})
        if(!result.ok) return notify('Konfigurasi belum dapat diperiksa: '+text(result.error,64),'error')
        const lines=(result.reports??[]).flatMap((report:Result)=>[report.source+' · '+report.summary.total+' temuan',...report.findings.map((finding:Result)=>finding.severity+' · '+finding.code+' · '+finding.path)])
        await context.ui.dialog.alert({title:'Audit konfigurasi Harmonika',message:lines.join('\n')+'\n\nAudit hanya baca. Alias lama didukung normalizer native; temuan bukan bukti konfigurasi diabaikan. Nilai credential tidak ditampilkan.'})
      } finally {setBusy(false)}
    }
    async function trackJob() {
      const source=await context.ui.dialog.select({title:'Pantau pekerjaan yang sudah dibuat',options:[{title:'Coding workspace',value:'coding'},{title:'Workflow engine',value:'unified'},{title:'Gambar / RTX',value:'media'}]})
      if(!source) return
      const id=await context.ui.dialog.prompt({title:'Job ID',description:'Gunakan ID dari hasil engine. Ini hanya membaca pekerjaan yang sudah ada.'})
      if(!id) return
      const pid=clearProject();setBusy(true)
      try {
        const result=await call({action:'track_job',project_id:pid,source,job_id:id})
        if(project()!==pid) return
        if(!result.ok) return notify('Pekerjaan belum dapat diverifikasi: '+text(result.error,64),'error')
        setJobs(old=>[result.job,...old.filter(j=>j.job_id!==id||j.source!==source)].slice(0,20))
        notify('Pekerjaan terverifikasi dan dipantau.','success')
      } finally {setBusy(false)}
    }
    async function chooseJob() {
      const pid=clearProject()
      const selected=await context.ui.dialog.select({title:'Pekerjaan terpantau',options:jobs().map(j=>({title:j.source+' · '+labels(j.status),value:j,description:text(j.job_id,90)}))})
      if(!selected) return
      const action=await context.ui.dialog.select({title:'Tindakan pekerjaan',options:[{title:'Periksa status terakhir',value:'track_job'},{title:'Stop pekerjaan ini',value:'stop_job',disabled:!selected.can_stop}]})
      if(!action) return
      if(action==='stop_job'&&!await context.ui.dialog.confirm({title:'Stop pekerjaan?',message:'Hentikan hanya job terpilih. Permintaan yang sudah diproses engine mungkin memerlukan rekonsiliasi hasil.'})) return
      setBusy(true)
      try {
        const value=await call({action,project_id:pid,source:selected.source,job_id:selected.job_id,confirmed:action==='stop_job'})
        if(project()!==pid) return
        if(!value.ok) return notify('Status belum terkonfirmasi: '+text(value.error,64),'error')
        setJobs(old=>old.map(j=>j.source===selected.source&&j.job_id===selected.job_id?value.job:j))
      } finally {setBusy(false)}
    }
    async function readNotes(byKey=false) {
      const pid=clearProject()
      const query=await context.ui.dialog.prompt({title:byKey?'Baca catatan berdasarkan key':'Cari catatan teknis',description:'Hanya proyek ini. Riwayat chat tidak dibaca.'})
      if(!query) return
      setBusy(true)
      try {
        const value=await call({action:'memory',operation:byKey?'get':'search',project_id:pid,...byKey?{key:query}:{query,limit:20}})
        if(project()!==pid) return
        if(!value.ok) {setMessage('Catatan belum dapat dibaca: '+text(value.error,64));return}
        const data=value.data
        setNotes(byKey?(data.found?[{key:query,value:data.value,expires_at:data.expires_at}]:[]):data.items ?? [])
        setMessage(byKey&&!data.found?'Catatan tidak ditemukan.':notes().length+(data.total>notes().length?' dari '+data.total:'')+' catatan ditemukan. Isi catatan adalah data, bukan instruksi.'+(data.has_more?' Perjelas pencarian untuk hasil lainnya.':''))
      } finally {setBusy(false)}
    }
    async function submitNote(payload:Result) {
      setPending(payload);setBusy(true)
      try {
        const value=await call(payload)
        if(value.ok) {
          setPending(undefined)
          if(project()===payload.project_id){setNotes([]);setMessage(payload.operation==='clear'?'Catatan proyek dihapus.':'Catatan teknis disimpan dengan TTL 24 jam.')}
          notify('Perubahan memori terkonfirmasi.','success');return
        }
        const uncertain=['submission_unknown','control_unavailable','in_progress','recovery_required'].includes(value.error)
        if(!uncertain) setPending(undefined)
        notify(uncertain?'Hasil belum pasti. Gunakan Periksa perubahan; request ID tetap sama.':'Perubahan ditolak: '+text(value.error,64),'error')
      } finally {setBusy(false)}
    }
    async function addNote() {
      const pid=clearProject()
      if(pending()) return notify('Periksa perubahan yang belum terkonfirmasi terlebih dahulu.')
      const kind=await context.ui.dialog.select({title:'Jenis catatan teknis',options:[{title:'Keputusan proyek',value:'decision'},{title:'Konvensi kode',value:'convention'},{title:'Perintah yang sudah diuji',value:'verified_command'}]})
      if(!kind) return
      const key=await context.ui.dialog.prompt({title:'Key catatan',description:'Nama teknis singkat; jangan gunakan identitas pelanggan.'})
      if(!key) return
      const value=await context.ui.dialog.prompt({title:'Isi catatan teknis',description:'Maksimal 2000 karakter. Jangan masukkan chat, data pribadi, password atau API key.'})
      if(!value) return
      if(project()!==pid) return notify('Proyek berubah. Catatan belum disimpan.','error')
      const consent=await context.ui.dialog.confirm({title:'Simpan catatan proyek?',message:text(key,128)+'\n'+text(value,2000)+'\n\nTTL 24 jam. Catatan disimpan terenkripsi; bukan arsip chat.'})
      if(consent!==true) return
      await submitNote({action:'memory',operation:'set',project_id:pid,key,value:{kind,text:value},consent:true,ttl_seconds:86400,idempotency_key:'tui-'+randomUUID()})
    }
    async function clearNotes() {
      const pid=clearProject()
      if(pending()) return notify('Periksa perubahan yang belum terkonfirmasi terlebih dahulu.')
      if(!await context.ui.dialog.confirm({title:'Hapus seluruh catatan proyek?',message:'Semua catatan teknis untuk proyek dan token ini akan dihapus. Sesi chat tidak dihapus.'})) return
      if(project()!==pid) return
      await submitNote({action:'memory',operation:'clear',project_id:pid,all:true,idempotency_key:'tui-'+randomUUID()})
    }
    async function reconcileNote() {
      const payload=pending()
      if(!payload) return
      if(payload.project_id!==project()) return notify('Perubahan berasal dari proyek lain. Buka kembali proyek tersebut sebelum memeriksa hasilnya.','error')
      if(!await context.ui.dialog.confirm({title:'Periksa perubahan yang sama?',message:'Menggunakan kembali request ID dan input asli. Tidak membuat operasi baru.'})) return
      await submitNote(payload)
    }
    const Action=(props:{label:string;run:()=>unknown;disabled?:boolean;maxWidth?:number}) => <text fg={props.disabled?context.theme.text.muted:context.theme.text.action.primary.base} wrapMode="none" truncate selectable={false} maxWidth={props.maxWidth} onMouseUp={event=>{event.preventDefault();event.stopPropagation();if(event.button===0&&!props.disabled)void props.run()}}>{'['+props.label+']'}</text>
    function Body(props:{panelWidth:number;onClose:()=>void;focused?:boolean}) {
      const section=()=>tab()
      const actions=()=>[
        {label:busy()?'Menunggu…':'Segarkan',run:()=>refresh(true),disabled:busy()},
        ...section()==='jobs'?[
          {label:'Stop respons',run:stopSession},
          {label:'Pantau job',run:trackJob,disabled:busy()},
          {label:'Pilih job',run:chooseJob,disabled:busy()||!jobs().length},
        ]:[],
        ...section()==='memory'?[
          {label:'Cari',run:()=>readNotes(),disabled:busy()},
          {label:'Baca key',run:()=>readNotes(true),disabled:busy()},
          {label:'Simpan',run:addNote,disabled:busy()},
          {label:'Hapus proyek',run:clearNotes,disabled:busy()},
          ...pending()?[{label:'Periksa perubahan',run:reconcileNote,disabled:busy()}]:[],
        ]:[],
        ...section()==='connection'?[
          {label:'Sambungkan ulang',run:reconnect,disabled:busy()},
          {label:'Periksa konfigurasi',run:auditConfig,disabled:busy()},
        ]:[],
      ]
      const rows=()=>actionRows(actions(),props.panelWidth)
      return <box flexDirection="column" width="100%" height="100%" minHeight={0} paddingLeft={1} paddingRight={1}>
        <box flexDirection="row" justifyContent="space-between" height={1} flexShrink={0}>
          <text fg={context.theme.text.base} flexGrow={1} flexShrink={1} wrapMode="none" truncate>{'Harmonika · '+names[section()]}</text>
          <Action label="Tutup" run={props.onClose}/>
        </box>
        <box flexDirection="row" gap={1} height={1} flexShrink={0} marginBottom={1}>
          <For each={Object.keys(names) as Tab[]}>{key=><Action label={(key===section()?'● ':'')+(props.panelWidth<70?({engine:'Engine',jobs:'Job',memory:'Memori',connection:'Koneksi'})[key]:names[key])} run={()=>open(key)}/>}</For>
        </box>
        <scrollbox flexGrow={1} flexShrink={1} minHeight={1} width="100%" focused={props.focused ?? true}>
          <Show when={section()==='engine'}>
            <text fg={context.theme.text.muted}>Koneksi nyata · metadata saja</text>
            <text fg={tone(health().backend?.status)}>{'Backend   '+labels(health().backend?.status)}</text>
            <text fg={tone(health().graphics?.status)}>{'RTX       '+labels(health().graphics?.status)}</text>
            <text fg={context.theme.text.base}>{'Model     '+text(context.ui.model.current()?.modelID ?? '— belum dipilih',70)}</text>
            <text fg={context.theme.text.muted}>{'Probe     '+(health().backend?.latency_ms != null?health().backend.latency_ms+' ms':'—')}</text>
            <text fg={context.theme.text.muted}>{'Registry  '+labels(health().registry?.status)+(Number.isInteger(health().registry?.registered)?' · '+health().registry.registered+' terdaftar':'')}</text>
            <text fg={context.theme.text.muted}>{'Diperiksa '+(health().checked_at?new Date(health().checked_at*1000).toLocaleTimeString('id-ID'):'—')+(health().checked_at&&Date.now()/1000-health().checked_at>75?' · status lama':'')}</text>
            <text fg={context.theme.text.base}>{'Sesi      '+labels(execution().status)}</text>
            <text fg={context.theme.text.muted}>{'Token     '+(Number(usage().input)>0||Number(usage().output)>0?String(usage().input??0)+' masuk / '+String(usage().output??0)+' keluar':'— belum dilaporkan')}</text>
            <text fg={context.theme.text.muted}>{'Biaya     '+(Number(usage().cost)>0?'$'+Number(usage().cost).toFixed(4):'— belum dilaporkan')}</text>
            <text fg={context.theme.text.muted}>Probe API bukan waktu respons model. Jumlah katalog bukan jumlah skill siap pakai.</text>
          </Show>
          <Show when={section()==='jobs'}>
            <text fg={context.theme.text.base}>{'Respons sesi · '+labels(execution().status)}</text>
            <text fg={context.theme.text.base}>Tahapan pekerjaan</text>
            <For each={workflow()?.observed?workflow()?.phases:execution().phases}>{(item:any)=><text fg={item.last_exit_code!=null&&item.last_exit_code!==0?context.theme.text.feedback.error.base:context.theme.text.muted}>{((workflow()?.active_phase??execution().active_phase)===item.id?'› ':'  ')+phaseLabels[item.id]+' · '+(item.status==='not_observed'?'Belum teramati':item.status==='declared'?'Dilaporkan agen':item.id==='finish'?(item.status==='completed'?'Respons selesai':labels(item.status)):item.source==='native'?'Respons dimulai':'Tool teramati')+(item.last_exit_code!=null?' · keluar '+item.last_exit_code:'')}</text>}</For>
            <text fg={context.theme.text.muted}>Tahap bukan bukti lulus. Build/tes perlu hasil perintah; akhir respons bukan jaminan proyek selesai.</text>
            <Show when={workflow()?.retry}><text fg={workflow()?.retry.retry?context.theme.text.muted:context.theme.text.feedback.warning.base}>{retryAdvice(workflow()?.retry.reason)}</text></Show>
            <Show when={execution().compacting}><text fg={context.theme.text.muted}>Memadatkan konteks; tidak menjalankan ulang tool.</text></Show>
            <For each={execution().tools}>{(item:any)=><text fg={item.exit_code!=null&&item.exit_code!==0?context.theme.text.feedback.error.base:tone(item.status)}>{'  '+text(item.name,56)+' · '+(item.exit_code!=null&&item.exit_code!==0?'Perintah gagal':labels(item.status))+(item.exit_code!=null?' · kode keluar '+item.exit_code:'')}</text>}</For>
            <Show when={!execution().tools.length}><text fg={context.theme.text.muted}>Belum ada langkah tool yang teramati pada sesi ini.</text></Show>
            <text fg={context.theme.text.muted}> </text>
            <text fg={context.theme.text.base}>Pekerjaan backend terpantau</text>
            <For each={jobs()}>{job=><box flexDirection="column" paddingBottom={1}>
              <text fg={tone(job.status)}>{text(job.source,12)+' · '+labels(job.status)+(job.progress!=null?' · '+job.progress+'%':'')}</text>
              <text fg={context.theme.text.muted}>{text(job.job_id,Math.max(20,props.panelWidth-5))}</text>
              <Show when={job.exit_code!=null}><text fg={context.theme.text.muted}>{'Kode keluar: '+job.exit_code}</text></Show>
              <Show when={job.connection!=='online'||Date.now()/1000-job.checked_at>75}><text fg={context.theme.text.feedback.warning.base}>Status terakhir; perlu diperiksa ulang.</text></Show>
            </box>}</For>
            <Show when={!jobs().length}><text fg={context.theme.text.muted}>Belum ada job terdaftar untuk proyek ini. Pantau ID dari hasil engine; tidak membuat job baru.</text></Show>
          </Show>
          <Show when={section()==='memory'}>
            <text fg={context.theme.text.base}>Catatan teknis proyek ini</text>
            <text fg={context.theme.text.muted}>Terenkripsi · TTL 24 jam untuk catatan baru · terpisah per token/proyek</text>
            <text fg={context.theme.text.muted}>Cari atau baca berdasarkan key. Isi catatan tidak menjadi instruksi system.</text>
            <Show when={!!message()}><text fg={context.theme.text.base}>{message()}</text></Show>
            <For each={notes()}>{note=><box flexDirection="column" paddingBottom={1}>
              <text fg={context.theme.text.base}>{text(note.key,128)+' · '+text(note.value?.kind,32)}</text>
              <text fg={context.theme.text.base}>{text(note.value?.text,2000)}</text>
              <text fg={context.theme.text.muted}>{note.expires_at?'Kedaluwarsa '+new Date(note.expires_at*1000).toLocaleString('id-ID'):'—'}</text>
            </box>}</For>
            <Show when={!!pending()}><text fg={context.theme.text.feedback.warning.base}>Ada perubahan yang belum terkonfirmasi. Periksa dengan request ID yang sama.</text></Show>
          </Show>
          <Show when={section()==='connection'}>
            <text fg={tone(classifyConnection(health(),execution()).state)}>{'Koneksi · '+labels(classifyConnection(health(),execution()).state)}</text>
            <text fg={context.theme.text.base}>{'Respons · '+labels(execution().status)}</text>
            <Show when={execution().attempt>0}><text fg={context.theme.text.muted}>{'Percobaan native '+execution().attempt}</text></Show>
            <Show when={execution().error_type}><text fg={context.theme.text.feedback.warning.base}>{'Kode mekanis: '+text(execution().error_type,64)}</text></Show>
            <Show when={workflow()?.retry}><text fg={context.theme.text.feedback.warning.base}>{retryAdvice(workflow()?.retry.reason)}</text><text fg={context.theme.text.muted}>{'Retry '+(workflow()?.retry.retry?'diizinkan sementara':'dihentikan')+' · percobaan '+workflow()?.retry.attempt+' · '+text(workflow()?.retry.code,64)}</text></Show>
            <text fg={context.theme.text.muted}>Sambungkan ulang hanya memperbarui metadata/status. Tugas dan mutation tidak dikirim ulang.</text>
            <text fg={context.theme.text.muted}>Respons terputus setelah teks tampil perlu diperiksa. Tidak ada pergantian provider atau fallback lokal diam-diam.</text>
            <text fg={context.theme.text.muted}>401/403 dan batas layanan memerlukan pemeriksaan akses; tidak dicoba melalui provider lain.</text>
          </Show>
        </scrollbox>
        <box flexDirection="column" height={rows().length} flexShrink={0} marginTop={1}>
          <For each={rows()}>{row=><box flexDirection="row" gap={1} height={1} flexShrink={0}><For each={row}>{action=><Action label={action.label} run={action.run} disabled={action.disabled} maxWidth={Math.max(8,props.panelWidth-2)}/>}</For></box>}</For>
        </box>
        <text fg={context.theme.text.muted} height={1} flexShrink={0} wrapMode="none" truncate>{props.panelWidth<58?'F8 Job · F9 Memori · F10 Engine':'F8 Pekerjaan · F9 Memori · F10 Engine · Shift+F10 Koneksi'}</text>
      </box>
    }
    const cleanup = [
      context.data.listen(event=>{if(monitor.accept(event.details))setRevision(v=>v+1)}),
      context.ui.router.register({name:'harmonika-control',render:()=> <Body panelWidth={width()} onClose={close}/>}),
      context.ui.slot({append:'session.panel',render:input=> <Show when={input.name==='harmonika-control'}><Body panelWidth={input.width} onClose={input.close} focused={input.focused}/></Show>}),
      context.ui.slot({append:'prompt.footer.status',render:()=> <text fg={context.theme.text.muted} wrapMode="none" truncate maxWidth={Math.max(12,Math.min(22,Math.floor(width()/4)))} flexShrink={1} onMouseUp={()=>open('engine')}>{'AI '+(health().backend?.status==='online'?'●':health().backend?.status==='not_checked'?'…':'!')+' RTX '+(health().graphics?.status==='online'?'●':health().graphics?.status==='busy'?'…':health().graphics?.status==='not_checked'||health().graphics?.status==='unknown'?'?':'!')+' · F10'}</text>}),
      context.ui.slot({append:'app',render:()=>{
        context.keymap.layer(()=>({mode:'global',commands:(Object.keys(names) as Tab[]).map((key,index)=>({id:'harmonika.control.'+key,title:'Harmonika: '+names[key],group:'Harmonika',palette:true,slash:{name:({engine:'engine',jobs:'jobs',memory:'memory',connection:'connection'})[key]},bind:({engine:'f10',jobs:'f8',memory:'f9',connection:'shift+f10'})[key],run:()=>open(key)}))}))
        context.keymap.layer(()=>({mode:'global',enabled:()=>opened(),commands:[
          {id:'harmonika.control.refresh',title:'Harmonika: segarkan status',bind:'ctrl+r',run:()=>refresh(true)},
          {id:'harmonika.control.close',title:'Tutup panel Harmonika',bind:'escape',enabled:()=>context.keymap.mode.current()==='base',run:close},
          {id:'harmonika.control.note.search',title:'Harmonika: cari catatan proyek',palette:true,run:()=>readNotes()},
          {id:'harmonika.control.note.read',title:'Harmonika: baca key catatan',palette:true,run:()=>readNotes(true)},
          {id:'harmonika.control.note.add',title:'Harmonika: simpan catatan teknis',palette:true,run:addNote},
          {id:'harmonika.control.note.clear',title:'Harmonika: hapus catatan proyek',palette:true,run:clearNotes},
          {id:'harmonika.control.job.track',title:'Harmonika: pantau job backend',palette:true,run:trackJob},
          {id:'harmonika.control.job.select',title:'Harmonika: pilih job backend',palette:true,run:chooseJob},
          {id:'harmonika.control.stop',title:'Harmonika: Stop respons sesi',palette:true,run:stopSession},
          {id:'harmonika.control.reconnect',title:'Harmonika: sambungkan ulang metadata',palette:true,run:reconnect},
          {id:'harmonika.control.config.audit',title:'Harmonika: audit konfigurasi',palette:true,run:auditConfig},
        ]}))
        return null
      }}),
    ]
    const resize=()=>{setWidth(context.renderer.width)}
    context.renderer.on('resize',resize)
    const interval=setInterval(()=>{setRevision(v=>v+1);clearProject();void refresh();void refreshRuntime()},5000)
    void refresh(true)
    return ()=>{stopped=true;clearInterval(interval);context.renderer.removeListener('resize',resize);for(const child of processes)child.kill();cleanup.reverse().forEach(fn=>fn())}
  },
})
