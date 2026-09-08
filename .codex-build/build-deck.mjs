import fs from 'node:fs/promises';
import path from 'node:path';
import { Presentation, PresentationFile } from '@oai/artifact-tool';
import { pathToFileURL } from 'node:url';

const workspaceDir = '/Users/lmatas/source/dark-deployer';
const SKILL_DIR = '/Users/lmatas/.codex/plugins/cache/openai-primary-runtime/presentations/26.903.11726/skills/presentations';
const TMP_DIR = path.join(workspaceDir, '.codex-build');
const FINAL_PPTX = path.join(workspaceDir, 'presentacion-output/dARK-arquitectura-y-minter-api-v2.pptx');
const { resolvePresentationFont, finalizePresentation } = await import(pathToFileURL(path.join(SKILL_DIR,'container_tools/artifact_tool_utils.mjs')).href);
const font = resolvePresentationFont({ availableFonts: ['Aptos','Arial','Helvetica'] });
const ppt = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const C = { navy:'#102A43', blue:'#2F80ED', teal:'#18A999', orange:'#F2994A', ink:'#243B53', muted:'#627D98', pale:'#F4F7FB', line:'#D9E2EC', white:'#FFFFFF', red:'#D64545', purple:'#7654A5' };
function box(slide, x,y,w,h, text, opts={}) { const s=slide.shapes.add({geometry:opts.geometry||'roundRect',position:{left:x,top:y,width:w,height:h},fill:opts.fill||C.white,line:{fill:opts.line||'none',width:opts.lineWidth||0}}); s.text=text; s.text.style={typeface:font,fontSize:opts.size||22,bold:opts.bold||false,color:opts.color||C.ink,align:opts.align||'left',verticalAlign:opts.valign||'mid',autoFit:'shrink'}; return s; }
function line(slide,x1,y1,x2,y2,color=C.line,width=2){ return slide.shapes.add({geometry:'line',position:{left:Math.min(x1,x2),top:Math.min(y1,y2),width:Math.max(1,Math.abs(x2-x1)),height:Math.max(1,Math.abs(y2-y1))},line:{fill:color,width}}); }
function title(slide, t, sub='', light=false){ box(slide,64,34,1152,54,t,{geometry:'textbox',fill:'none',size:32,bold:true,color:light?C.white:C.navy}); if(sub) box(slide,66,91,1100,30,sub,{geometry:'textbox',fill:'none',size:16,color:light?'#9FB3C8':C.muted}); }
function footer(slide,n){ box(slide,1120,680,100,20,`dARK 2.0  •  ${n}`,{geometry:'textbox',fill:'none',size:12,color:C.muted,align:'right'}); }
function notes(slide, txt){ slide.speakerNotes.textFrame.setText(txt); }

// 1 cover
{ const s=ppt.slides.add(); s.background.fill=C.navy; box(s,74,106,900,80,'dARK 2.0',{geometry:'textbox',fill:'none',size:54,bold:true,color:C.white}); box(s,78,198,900,48,'Arquitectura y API de Minter',{geometry:'textbox',fill:'none',size:30,color:'#B9D7FF'}); box(s,80,290,520,74,'Una plataforma de identificadores ARK con control de autoridad, almacenamiento IPFS y publicación on-chain',{geometry:'textbox',fill:'none',size:22,color:'#E6F0FA'}); box(s,80,612,400,28,'Basado en la documentación del repositorio dark-deployer',{geometry:'textbox',fill:'none',size:16,color:'#9FB3C8'}); box(s,930,120,220,220,'dARK',{fill:C.blue,size:42,bold:true,color:C.white,align:'center'}); box(s,965,365,150,64,'Minter\n:8001',{fill:C.orange,size:22,bold:true,color:C.white,align:'center'}); }

// 2 map
{ const s=ppt.slides.add(); s.background.fill=C.white; title(s,'dARK en una vista','Tres capas separan autoridad, persistencia y resolución');
  box(s,80,155,1120,110,'Capa de servicios\nAdmin API :8000     Minter API :8001     Resolver API :8002     Store API :8003',{fill:'#EAF2FF',size:26,bold:true,color:C.navy,align:'center'});
  box(s,80,302,1120,110,'Capa blockchain\nAuthority.sol  ↔  IAuthority.sol  ↔  dARK.sol\nBesu / dark-env',{fill:'#EAF8F5',size:26,bold:true,color:'#0B625B',align:'center'});
  box(s,80,449,1120,110,'Capa de almacenamiento\ndark-store-api  →  IPFS Cluster  →  nodos IPFS',{fill:'#FFF3E8',size:26,bold:true,color:'#8A4B08',align:'center'});
  line(s,640,265,640,302,C.blue,4); line(s,640,412,640,449,C.orange,4); footer(s,2); notes(s,'Fuentes: DARK_2.0_ARCHITECTURE.md; docs/ipfs-concepts-and-dark-store-api.md'); }

// 3 contracts
{ const s=ppt.slides.add(); s.background.fill=C.pale; title(s,'Contratos inteligentes','La arquitectura separa control de acceso y almacenamiento de ARKs');
  box(s,75,160,330,340,'Authority.sol\n\n• UUID ↔ wallet\n• estado activo\n• autorización de NAAN\n• clave privada cifrada\n\nAdmin registra y desactiva.\nLa autoridad reclama NAANs.',{fill:'#EAF8F5',size:21,bold:false,color:C.ink});
  box(s,475,250,330,145,'IAuthority.sol\n\nis_authorized(wallet, naan)\nis_active_authority(wallet)',{fill:C.white,size:22,bold:true,color:C.purple,align:'center'});
  box(s,875,160,330,340,'dARK.sol\n\n• create_ark\n• update_ark\n• resolve\n• get_ark\n• ark_exists\n\nGuarda name, naan, url, cid, owner y timestamps.',{fill:'#EAF2FF',size:21,color:C.ink});
  line(s,405,320,475,320,C.teal,4); line(s,805,320,875,320,C.teal,4); footer(s,3); notes(s,'Fuentes: DARK_2.0_ARCHITECTURE.md, secciones 2.1–2.3; DARK_2.0_GUIDE.md, sección 4.'); }

// 4 infra
{ const s=ppt.slides.add(); s.background.fill=C.white; title(s,'Topología de despliegue','En sandbox y producción, los tiers escalan por separado');
  box(s,70,145,1140,125,'Blockchain tier\n≥3 nodos Besu / QBFT / PoA\nNode 1: bootnode + RPC + despliegue de contratos',{fill:'#EAF2FF',size:24,bold:true,color:C.navy,align:'center'});
  box(s,70,330,1140,125,'Application tier\nAdmin API · Minter API · Resolver API · Store API · Dashboard',{fill:'#F4F7FB',size:24,bold:true,color:C.ink,align:'center'});
  box(s,70,515,1140,95,'IPFS tier\nKubo + IPFS Cluster; el Cluster replica el contenido fijado',{fill:'#FFF3E8',size:24,bold:true,color:'#8A4B08',align:'center'});
  line(s,640,270,640,330,C.blue,4); line(s,640,455,640,515,C.orange,4); footer(s,4); notes(s,'Fuente: docs/decoupled-infrastructure.md. Nota: el documento se marca como histórico respecto al diseño de almacenamiento; se conserva aquí la separación de tiers y el modelo de despliegue.'); }

// 5 lifecycle
{ const s=ppt.slides.add(); s.background.fill=C.pale; title(s,'Ciclo de vida de un ARK','El Minter desacopla la reserva del identificador y la confirmación on-chain');
  const xs=[90,320,550,780,1010]; const labels=[['R','reserved','NOID asigna ID'],['D','draft','metadata recibida'],['U','update','cambio pendiente'],['P','published','confirmado on-chain'],['T','tombstone','borrado lógico']];
  labels.forEach((a,i)=>{ box(s,xs[i],215,150,92,a[0],{fill:i===3?C.teal:i===4?C.red:C.blue,size:40,bold:true,color:C.white,align:'center'}); box(s,xs[i]-10,325,170,60,`${a[1]}\n${a[2]}`,{geometry:'textbox',fill:'none',size:18,bold:true,color:C.ink,align:'center'}); if(i<4) line(s,xs[i]+150,261,xs[i+1],261,C.muted,3); });
  box(s,120,495,1030,70,'reserve() → R     PUT metadata: R → D o P → U     workers: D/U → P     DELETE: cualquier estado → T',{fill:C.white,size:22,bold:true,color:C.navy,align:'center'}); footer(s,5); notes(s,'Fuentes: DARK_2.0_GUIDE.md sección 3; DARK_2.0_API_REFERENCE.md sección 2.1.'); }

// 6 API
{ const s=ppt.slides.add(); s.background.fill=C.white; title(s,'Minter API','Superficie REST para reservar, completar, consultar y retirar ARKs');
  box(s,70,145,1140,70,'POST  /arks                    Reserva un ARK con NOID',{fill:'#EAF2FF',size:24,bold:true,color:C.navy});
  box(s,70,235,1140,70,'POST  /arks/batch              Reserva varios y correlaciona por client_item_id',{fill:'#EAF2FF',size:24,bold:true,color:C.navy});
  box(s,70,325,1140,70,'GET   /arks/{ark}              Consulta DB; fallback y merge con blockchain',{fill:'#F4F7FB',size:24,bold:true,color:C.ink});
  box(s,70,415,1140,70,'PUT   /arks/{ark}              Guarda metadata y encola create/update',{fill:'#EAF8F5',size:24,bold:true,color:'#0B625B'});
  box(s,70,505,1140,70,'DELETE /arks/{ark}              Tombstone irreversible, solo propietario',{fill:'#FFF3E8',size:24,bold:true,color:'#8A4B08'});
  box(s,70,625,1140,35,'Escrituras: identidad de autoridad. Lecturas: mTLS. Worker status: red interna.',{geometry:'textbox',fill:'none',size:18,color:C.muted,align:'center'}); footer(s,6); notes(s,'Fuente: DARK_2.0_API_REFERENCE.md sección 2.2.'); }

// 7 example
{ const s=ppt.slides.add(); s.background.fill=C.navy; title(s,'Ejemplo de integración','La API devuelve el estado operativo y los CIDs cuando están disponibles',true);
  box(s,78,145,1120,72,'PUT /arks/ark:/12345/abc123',{fill:C.blue,size:24,bold:true,color:C.white});
  box(s,78,242,1120,250,'{\n  "authority_id": "org-uuid-12345",\n  "target": "https://example.org/item/1",\n  "minimal_metadata": {\n    "ark": "ark:/12345/abc123", "title": "My Item"\n  },\n  "original_metadata": "<oai_dc:dc>...</oai_dc:dc>",\n  "metadata_schema": "dublin_core",\n  "metadata_media_type": "application/xml"\n}',{fill:'#173B5B',size:20,color:'#E6F0FA'});
  box(s,78,535,1120,70,'Respuesta: ARKResponse  |  state: D  |  level1_cid: null  |  level2_cid: null',{fill:C.orange,size:22,bold:true,color:C.white,align:'center'}); footer(s,7); notes(s,'Fuente: DARK_2.0_API_REFERENCE.md sección 2.2. El ejemplo se adapta mínimamente del payload documentado.'); }

// 8 workers/storage
{ const s=ppt.slides.add(); s.background.fill=C.pale; title(s,'Workers y persistencia','El Minter mantiene PostgreSQL como cola de trabajo y usa Store API como frontera de almacenamiento');
  box(s,65,175,245,145,'API\nreserva / recibe metadata',{fill:C.blue,size:24,bold:true,color:C.white,align:'center'});
  box(s,375,145,250,205,'PostgreSQL\n\nEstado del ARK\nreintentos\nerrores permanentes',{fill:C.white,size:23,bold:true,color:C.navy,align:'center'});
  box(s,690,100,250,105,'Metadata worker\nL1/L2 → Store API',{fill:'#EAF8F5',size:22,bold:true,color:'#0B625B',align:'center'});
  box(s,690,245,250,105,'Replication worker\nreconciliación IPFS/Cluster',{fill:'#FFF3E8',size:22,bold:true,color:'#8A4B08',align:'center'});
  box(s,995,175,210,145,'Chain worker\ncreate/update → Besu',{fill:'#EAF2FF',size:22,bold:true,color:C.navy,align:'center'});
  line(s,310,247,375,247,C.muted,3); line(s,625,220,690,152,C.teal,3); line(s,625,275,690,297,C.orange,3); line(s,940,247,995,247,C.blue,3);
  box(s,110,470,1060,75,'Para los servicios de negocio: Minter / Resolver → Store API → IPFS / Cluster',{fill:C.white,size:25,bold:true,color:C.navy,align:'center'}); footer(s,8); notes(s,'Fuentes: DARK_2.0_ARCHITECTURE.md sección 5; docs/ipfs-concepts-and-dark-store-api.md secciones 7 y 8; docs/deployer-operations.md.'); }

// 9 chain publishing
{ const s=ppt.slides.add(); s.background.fill=C.white; title(s,'Publicación on-chain','El pipeline de transacciones maximiza throughput sin romper la secuencia de nonces');
  box(s,70,165,215,110,'Operaciones\ncreate / update',{fill:C.blue,size:24,bold:true,color:C.white,align:'center'});
  box(s,350,165,215,110,'Ventana de 20\nnonce N…N+19',{fill:'#EAF2FF',size:24,bold:true,color:C.navy,align:'center'});
  box(s,630,165,215,110,'Firmar y enviar\nal mempool',{fill:'#EAF8F5',size:24,bold:true,color:'#0B625B',align:'center'});
  box(s,910,165,260,110,'Recibos y estados\nconfirmed / reverted / ambiguous',{fill:'#FFF3E8',size:22,bold:true,color:'#8A4B08',align:'center'});
  line(s,285,220,350,220,C.muted,3); line(s,565,220,630,220,C.muted,3); line(s,845,220,910,220,C.muted,3);
  box(s,90,380,1080,110,'Regla de seguridad: un fallo de build, envío o receipt ambiguo detiene las ventanas siguientes y las marca not_sent para evitar un nonce gap.',{fill:C.red,size:24,bold:true,color:C.white,align:'center'});
  box(s,100,555,1080,55,'Resultado por operación: ref · acción · estado · error opcional',{geometry:'textbox',fill:'none',size:22,bold:true,color:C.navy,align:'center'}); footer(s,9); notes(s,'Fuente: DARK_2.0_ARCHITECTURE.md sección 4.1.'); }

const candidate = path.join(TMP_DIR,'candidate.pptx');
await (await PresentationFile.exportPptx(ppt)).save(candidate);
const requirements={ explicitTotalSlideCount:9, requiredNativeTableOwnerSlides:[], requiredNativeChartOwnerSlides:[], fontPolicy:{basis:'design',families:[font]} };
const stagingDir=path.join(workspaceDir,'.codex-finalizer'); await fs.mkdir(stagingDir,{recursive:true}); await fs.mkdir(path.dirname(FINAL_PPTX),{recursive:true});
const result=await finalizePresentation({ ...requirements, workspaceDir, candidatePath:candidate, finalPath:FINAL_PPTX, pythonExecutable:'/Users/lmatas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3', integrityValidatorPath:path.join(SKILL_DIR,'container_tools/inspect_presentation_package_integrity.py'), layoutValidatorPath:path.join(SKILL_DIR,'container_tools/inspect_presentation_layout_geometry.py'), layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit'], fontPolicy:requirements.fontPolicy, verifyArtifactToolImport:true, receiptPath:path.join(stagingDir,'dARK-arquitectura-y-minter-api-v2.validation.json') });
console.log(JSON.stringify(result));
