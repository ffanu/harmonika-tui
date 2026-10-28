"""Account for every numbered section, including alternatives, without running tutorial shell."""
import hashlib,json,re
from pathlib import Path
DECISIONS={
'2':'existing_branded_tui_preserved','3':'existing_gateway_plus_isolated_python_runtime','4':'cpu_libraries_and_existing_mentor_rtx_adapters','5':'existing_sqlite_and_explicit_infrastructure_profile','6':'isolated_document_and_media_libraries','7':'existing_registered_network_connectors','8':'browser_capture_hardware_qualification_required','9':'existing_windows_rtx_workers_no_gpu_on_linux','10':'metadata_only_observability_no_customer_logs','11':'existing_docker_systemd_tls_no_host_replacement','12':'defensive_only_existing_policy_preserved','13':'isolated_connector_libraries_credentials_required','14':'replaced_by_isolated_hash_locked_installer','15':'real_fixture_verification','16':'explicit_loopback_infrastructure_profile','17':'existing_internal_interface_no_tui_rewrite','18':'dual_pc_resource_limits','19':'capability_readiness_not_installation_only','1':'existing_decoupled_dual_pc_architecture'}
def build(text):
 parts=[];sections=[]
 for line in text.splitlines():
  match=re.match(r'\s*PART\s+(\d+):\s*(.+)',line)
  if match:parts.append({'part':int(match[1]),'title':match[2].strip(),'decision':DECISIONS[match[1]]})
  match=re.match(r'^(\d+)\.(\d+)\s+(.+)',line)
  if match:sections.append({'id':match[1]+'.'+match[2],'title':match[3].strip(),'decision':DECISIONS.get(match[1],'review_required')})
 return {'schema_version':1,'source_sha256':hashlib.sha256(text.encode()).hexdigest(),'parts':parts,'sections':sections,'guide_commands_executed':False,'corrections':{'asyncio':'Python stdlib; no pip installation','zipfile36':'Python stdlib zipfile','PyPDF2':'pypdf maintained successor','google-generativeai':'google-genai current SDK','id_core_news_sm':'no official Indonesian pretrained spaCy pipeline','aioredis':'redis.asyncio','torch_vllm':'GPU models stay in registered external worker; not installed in CPU gateway','firewall_script':'not applied; would conflict with custom SSH/VPN ports','default_passwords':'not used','TUI_frameworks':'alternatives; custom binary preserved'}}
if __name__=='__main__':
 import sys
 print(json.dumps(build(Path(sys.argv[1]).read_text()),indent=2))
