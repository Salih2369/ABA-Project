"""Real loopback HTTP integration tests; no model download required."""
import http.client
import importlib
import json
import threading
import unittest
import re
import subprocess
from pathlib import Path
import tempfile
from unittest.mock import patch as mock_patch


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('aba_demo.server')
        self.server = self.module.create_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, payload=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        data = raw if raw is not None else (json.dumps(payload) if payload is not None else None)
        h = {'Content-Type': 'application/json'}
        h.update(headers or {})
        connection.request(method, path, body=data, headers=h)
        response = connection.getresponse()
        body = response.read().decode('utf-8')
        status = response.status
        connection.close()
        return status, body

    def post(self, path, payload=None, raw=None, headers=None):
        h = {'X-ABA-Token': self.server.token}
        h.update(headers or {})
        return self.request('POST', path, payload, h, raw)

    @staticmethod
    def context_document():
        source = 'a' * 64
        return {
            'schema_version': 1,
            'source_sha256': source,
            'tracking_candidate_sha256': 'b' * 64,
            'provenance': {
                'adapter': 'local_transformers',
                'source_sha256': source,
                'requested_model': 'vendor/local-model',
                'resolved_model': 'vendor/local-model',
                'endpoint_provider': None,
                'external_processing': False,
                'structured_outputs': False,
                'data_collection': 'local',
                'zero_data_retention_required': False,
                'sent_fields': [],
                'consent': {'required': False, 'granted': False,
                            'source_sha256': None},
            },
            'context_segments': [{
                'segment_id': 'ctx-1', 'start_time': 1.0, 'end_time': 2.0,
                'activity_suggestion': 'table', 'activity_status': 'supported',
                'target_material_interaction': 'yes',
                'adult_target_interaction_visible': 'ambiguous',
                'evidence_times': [1.0, 1.5],
                'clinician_confirmation': 'pending',
            }],
        }

    def test_context_validate_accepts_exact_raw_document_without_state_mutation(self):
        self.assertEqual(self.post('/api/reset', {'config': {}})[0], 200)
        self.assertEqual(self.post('/api/activity', {'activity': 'movement'})[0], 200)
        engine = self.server.engine
        last_analysis = {'sentinel': True}
        analyzer = unittest.mock.Mock()
        self.server.last_analysis = last_analysis
        self.server.analyzer = analyzer
        raw = json.dumps(self.context_document(), ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')

        status, body = self.post('/api/context/validate', raw=raw, headers={
            'X-ABA-Source-Duration': '3.0'})

        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {'status': 'valid'})
        self.assertIs(self.server.engine, engine)
        self.assertEqual(self.server.engine.activity, 'movement')
        self.assertIs(self.server.analyzer, analyzer)
        self.assertIs(self.server.last_analysis, last_analysis)

    def test_context_validate_rejects_invalid_headers_json_schema_and_security(self):
        raw = json.dumps(self.context_document(), separators=(',', ':'))
        duration = {'X-ABA-Source-Duration': '3'}
        invalid_raw = (
            '[]', 'null', '{bad', '{"schema_version":1,"schema_version":1}',
            raw.replace('"segment_id":"ctx-1"', '"segment_id":"ctx-1","segment_id":"ctx-2"'),
            raw.replace('"schema_version":1', '"schema_version":2'),
            raw.replace('"end_time":2.0', '"end_time":4.0'),
            raw.replace('"activity_suggestion":"table"', '"activity_suggestion":"diagnosis"'),
        )
        for candidate in invalid_raw:
            with self.subTest(candidate=candidate[:40]):
                self.assertEqual(self.post('/api/context/validate', raw=candidate,
                                           headers=duration)[0], 400)
        for value in (None, '', '0', '-1', 'NaN', 'Infinity', 'not-a-number'):
            with self.subTest(duration=value):
                headers = {} if value is None else {'X-ABA-Source-Duration': value}
                self.assertEqual(self.post('/api/context/validate', raw=raw,
                                           headers=headers)[0], 400)
        self.assertEqual(self.post('/api/context/validate', raw=raw, headers={
            **duration, 'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/api/context/validate', raw=raw,
                                      headers=duration)[0], 403)
        self.assertEqual(self.request('POST', '/api/context/validate', raw=raw, headers={
            **duration, 'X-ABA-Token': self.server.token,
            'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.post('/api/context/validate', raw='', headers={
            **duration,
            'Content-Length': str(self.module.MAX_CONTEXT_JSON_BYTES + 1)})[0], 413)

    def test_context_validate_has_independent_twenty_mib_body_limit(self):
        raw = json.dumps(self.context_document(), separators=(',', ':')).encode('utf-8')
        padded = raw + b' ' * (self.module.MAX_JSON_BYTES + 1 - len(raw))
        status, body = self.post('/api/context/validate', raw=padded, headers={
            'X-ABA-Source-Duration': '3'})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {'status': 'valid'})

    def test_payloads_are_bounded_strict_json_objects(self):
        for raw in ('[]', 'null', '42', '{bad', '{"x":NaN}', '{"x":1,"x":2}'):
            with self.subTest(raw=raw):
                self.assertEqual(self.post('/api/reset', raw=raw)[0], 400)
        self.assertEqual(self.post('/api/reset', raw='{}', headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.post('/api/reset', raw='', headers={'Content-Length': str(self.module.MAX_JSON_BYTES + 1)})[0], 413)
        self.assertEqual(self.post('/api/reset', {'config': []})[0], 400)

    def test_rejected_content_type_with_body_returns_reliably(self):
        def post(server, raw, content_type='application/json'):
            port = server.server_address[1]
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request(
                'POST', '/api/reset', body=raw,
                headers={'Content-Type': content_type,
                         'X-ABA-Token': server.token})
            response = connection.getresponse()
            status = response.status
            response.read()
            connection.close()
            return status

        for attempt in range(64):
            server = self.module.create_server(port=0)
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={'poll_interval': .001}, daemon=True)
            thread.start()
            try:
                for raw in ('[]', 'null', '42', '{bad', '{"x":NaN}',
                            '{"x":1,"x":2}'):
                    self.assertEqual(post(server, raw), 400)
                with self.subTest(attempt=attempt):
                    self.assertEqual(post(server, '{}', 'text/plain'), 415)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_engine_reset_activity_observe_flow(self):
        self.assertEqual(self.post('/api/reset', {'config': {}})[0], 200)
        self.assertEqual(self.post('/api/activity', {'activity': 'movement'})[0], 200)
        observation = {'time': 0, 'identity': 'confirmed', 'signals': {
            key: False for key in ('orientation', 'body_motion', 'out_of_seat', 'hand_motion', 'posture_change')}}
        status, body = self.post('/api/observe', observation)
        self.assertEqual(status, 200, body)
        state = json.loads(body)
        self.assertEqual(state['activity'], 'movement')
        self.assertEqual(len(state['indicators']), 5)
        self.assertIsNone(state['alert'])
        self.assertEqual(self.post('/api/observe', {'time': 'bad'})[0], 400)
        self.assertEqual(self.post('/api/activity', {'activity': 'invalid'})[0], 400)
        self.assertEqual(self.post('/api/reset', {'config': {}})[0], 200)
        status, body = self.post('/api/observe', observation)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['events'], [])

    def test_analysis_fails_closed_without_valid_local_video(self):
        for path in ('', '../README.md', 'https://example.com/video.mp4', '//server/share/video.mp4', 'C:/not-real/video.mp4'):
            with self.subTest(path=path):
                status, body = self.post('/api/open', {'path': path})
                self.assertEqual(status, 400, body)
        self.assertEqual(self.post('/api/select', {'x': .5, 'y': .5})[0], 409)
        self.assertEqual(self.post('/api/analyze', {'time': 0})[0], 409)

    def test_page_is_restricted_rtl_bootstrapped_static(self):
        status, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('dir="rtl"', body)
        self.assertIn(self.server.token, body)
        self.assertNotIn('__ABA_TOKEN__', body)
        for path in ('/../README.md', '/%2e%2e/README.md', '/aba_demo/server.py', '/static/../server.py', '/?path=C:/Windows/win.ini'):
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_analysis_io_contract_resets_target_caches_frames_and_closes(self):
        instances = []
        class FakeVideoAnalyzer:
            """IO contract double, deliberately not a model or inference fixture."""
            def __init__(self, path, config):
                self.closed = False
                instances.append(self)
            def preview(self):
                return {'time': 0, 'boxes': [], 'width': 100, 'height': 100}
            def select_target(self, x, y):
                return {'identity': 'confirmed', 'target_id': 1}
            def analyze(self, time):
                if time >= 10:
                    raise EOFError('End of video')
                return {'time': int(time), 'identity': 'confirmed', 'signals': {
                    key: key == 'body_motion' for key in ('orientation','body_motion','out_of_seat','hand_motion','posture_change')}}
            def close(self):
                self.closed = True
        with tempfile.NamedTemporaryFile(suffix='.mp4') as video, mock_patch('aba_demo.vision.VideoAnalyzer', FakeVideoAnalyzer):
            self.post('/api/reset', {'config': {'persistence_seconds': 1}})
            self.assertEqual(self.post('/api/open', {'path': video.name, 'config': {}})[0], 200)
            self.assertEqual(self.post('/api/select', {'x': .5, 'y': .5})[0], 200)
            self.assertEqual(self.post('/api/analyze', {'time': 0})[0], 200)
            status, first = self.post('/api/analyze', {'time': 1})
            self.assertEqual(status, 200, first)
            self.assertEqual(len(json.loads(first)['state']['events']), 1)
            status, duplicate = self.post('/api/analyze', {'time': 1.01})
            self.assertEqual(status, 200, duplicate)
            self.assertEqual(json.loads(duplicate)['state'], json.loads(first)['state'])
            self.post('/api/select', {'x': .5, 'y': .5})
            status, selected = self.post('/api/analyze', {'time': 1.01})
            self.assertEqual(status, 200, selected)
            self.assertEqual(json.loads(selected)['state']['events'], [])
            self.post('/api/activity', {'activity': 'movement'})
            self.assertEqual(self.post('/api/open', {'path': video.name})[0], 200)
            self.assertTrue(instances[0].closed)
            self.assertEqual(self.server.engine.config['persistence_seconds'], 1)
            self.assertEqual(self.server.engine.activity, 'movement')
            status, ended = self.post('/api/analyze', {'time': 10})
            self.assertEqual(status, 200, ended)
            self.assertEqual(json.loads(ended)['status'], 'end_of_video')
            self.server.shutdown()
            self.assertTrue(instances[-1].closed)
            self.server.server_close()
            self.assertTrue(instances[-1].closed)

    def test_write_security_rejects_untrusted_requests(self):
        status, _ = self.request('POST', '/api/reset', {})
        self.assertEqual(status, 403)
        status, _ = self.request('POST', '/api/reset', {}, headers={'X-ABA-Token': '\u00e9'})
        self.assertEqual(status, 403)
        status, _ = self.request('POST', '/api/reset', {}, headers={
            'X-ABA-Token': self.server.token, 'Origin': 'https://evil.example'})
        self.assertEqual(status, 403)
        status, _ = self.request('GET', '/health', headers={'Host': 'evil.example'})
        self.assertEqual(status, 403)

    def test_health_is_real_loopback_service(self):
        status, body = self.request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['status'], 'ok')
        self.assertEqual(self.server.server_address[0], '127.0.0.1')


class FrontendTests(unittest.TestCase):
    def run_js(self, assertions):
        html = (Path(__file__).parents[1] / 'aba_demo/static/index.html').read_text(encoding='utf-8')
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
        code = "const assert = require('node:assert/strict');\n" + '\n'.join(scripts) + '\n' + assertions
        result = subprocess.run(['node', '-'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_replay_serializes_seek_activity_and_stale_responses(self):
        self.run_js(r"""
(async()=>{
 let log=[], state=[], rendered=[], release;
 const api=async(path,payload)=>{
  log.push([path,payload]);
  if(path==='/api/reset') {state=[];return {};}
  if(path==='/api/observe') {state.push(payload.time); return {time:payload.time, seen:[...state]};}
  return {};
 };
 const player=new CausalPlayer(api,s=>rendered.push(s),e=>{throw e;});
 const rows=[0,1,2,3].map(time=>({time}));
 await player.configure(rows,'table');
 await player.seek(2);
 assert.deepEqual(rendered.filter(Boolean).at(-1).seen,[0,1,2]);
 await player.seek(.5);
 assert.deepEqual(rendered.filter(Boolean).at(-1).seen,[0]);
 await player.configure(rows,'movement');
 assert.equal(log.filter(x=>x[0]==='/api/activity').at(-1)[1].activity,'movement');
 assert.equal(log.filter(x=>x[0]==='/api/observe').some(x=>x[1].time===3),false);
 const slow=new CausalPlayer(async(path,payload)=>{
   if(path==='/api/observe'&&payload.time===2) await new Promise(r=>{release=r;});
   return api(path,payload);
 },s=>rendered.push(s),e=>{throw e;});
 await slow.configure(rows,'table');
 const pending=slow.seek(2);
 while(!release) await new Promise(r=>setTimeout(r,0));
 const marker=rendered.length;
 slow.seek(0); release(); await pending;
 assert.equal(rendered.slice(marker).filter(Boolean).some(s=>s.time>0),false);
 assert.deepEqual(rendered.filter(Boolean).at(-1).seen,[0]);
 console.log('causal replay and stale seek passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_activity_changes_are_time_local_and_survive_replay(self):
        self.run_js(r"""
(async()=>{
 let activity='table',events=[],last;
 const api=async(path,p)=>{
  if(path==='/api/reset') {events=[];return {};}
  if(path==='/api/activity') {activity=p.activity;return {};}
  if(path==='/api/observe') {if(activity==='table')events.push(p.time);return {time:p.time,events:[...events],activity};}
 };
 const player=new CausalPlayer(api,s=>{if(s)last=s;},e=>{throw e;});
 await player.configure([0,5,10,15].map(time=>({time})),'table');
 await player.seek(10);
 await player.setActivity('break',10);
 await player.seek(15);
 assert.deepEqual(last.events,[0,5]);
 assert.equal(last.activity,'break');
 await player.seek(5);assert.deepEqual(last.events,[0,5]);
 await player.seek(15);assert.deepEqual(last.events,[0,5]);
 assert.equal(player.activityAt(5),'table');assert.equal(player.activityAt(15),'break');
})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_dashboard_has_accessible_controls_and_honest_states(self):
        html = (Path(__file__).parents[1] / 'aba_demo/static/index.html').read_text(encoding='utf-8')
        for element in ('videoFile','jsonFile','contextFile','video','play','restart','activity','timeline',
                        'alertCard','snooze','reviewStatus','contextPanel','contextStatus','contextEmpty',
                        'contextCard','contextActivity','contextActivityStatus','contextMaterial',
                        'contextAdult','contextEvidence','contextReviewStatus','contextReviewPending',
                        'contextReviewConfirm','contextReviewReject','contextProvenance'):
            self.assertIn('id="' + element + '"', html)
        self.assertIn('سياق النشاط اليدوي', html)
        self.assertIn('السياق المرئي المقترح', html)
        self.assertIn('لا يصف الانتباه أو النية أو المشاعر', html)
        self.assertIn('ولا يقدم توصية علاجية', html)
        self.assertEqual(len(re.findall(r'data-indicator="', html)), 5)
        self.assertNotRegex(html, r'(?:src|href)="https?://')
        self.assertNotIn('.innerHTML', html)
        self.run_js(r"""
assert.equal(indicatorLabel('unobservable'),'غير قابل للرصد');
assert.equal(indicatorLabel('not_applicable'),'غير منطبق');
assert.equal(indicatorLabel(undefined),'غير قابل للرصد');
assert.equal(visibleAlert({evidence_time:2},10,60),null);
assert.deepEqual(visibleAlert({evidence_time:2},61,60),{evidence_time:2});
assert.equal(visibleAlert({evidence_time:20},10,0),null);
""")

    def test_dashboard_wires_exact_byte_context_import_without_replacing_tracking(self):
        html = (Path(__file__).parents[1] / 'aba_demo/static/index.html').read_text(encoding='utf-8')
        self.assertGreaterEqual(html.count("const rawHash=await sha256Hex(rawBytes)"), 2)
        self.assertIn("trackingHash=rawHash", html)
        self.assertIn("contextHash=rawHash", html)
        self.assertIn("validateContextBytes(fetch", html)
        self.assertIn("renderContextAt(video.currentTime)", html)
        self.assertIn("writeContextReview(localStorage", html)
        self.assertNotIn('المراجعة السياقية باستخدام VLM معطّلة', html)

    def test_structured_colab_provenance_remains_readable(self):
        self.run_js(r"""
assert.equal(provenanceLabel('SYNTHETIC TEST ONLY'),'SYNTHETIC TEST ONLY');
const audit=Array.from({length:5000},(_,frame_index)=>({frame_index,time:frame_index/10,identity:'confirmed'}));
const text=provenanceLabel({run_id:'r'.repeat(5000),adapter:'aba_demo.vision.VideoAnalyzer',
 weights:'yolo11n-pose.pt',tracker:'bytetrack.yaml',clinical_validation:false,
 config:{candidate_overlap_policy:'human_review_required',sample_hz:5},
 applied_target_reselections:[{target_id:6}],causal_audit:audit,
 quality:{passed:true,uncertain_frames:4,audited_frame_count:5000,uncertain_fraction:.0008}});
assert.ok(text.includes('aba_demo.vision.VideoAnalyzer'));
assert.ok(text.includes('\"clinical_validation\": false'));
assert.ok(text.includes('\"causal_audit_frame_count\": 5000'));
assert.ok(text.includes('\"target_reselection_count\": 1'));
assert.ok(text.includes('\"uncertain_frames\": 4'));
assert.ok(!text.includes('\"frame_index\": 4999'));
assert.ok(text.length < 2000,text.length);
assert.equal(provenanceLabel(undefined),'');
""")

    def test_import_rejects_nonfinite_metadata_before_replay(self):
        self.run_js(r"""
const row={time:0,identity:'confirmed',signals:Object.fromEntries(KEYS.map(k=>[k,null])),values:{nested:[1]}};
const data={schema_version:1,mode:'precomputed',source:{sha256:'a'.repeat(64)},observations:[row]};
assert.throws(()=>parseDataset(JSON.stringify(data).replace('"nested":[1]','"nested":[1e999]')));
""")

    def test_precomputed_schema_hash_and_causal_slice(self):
        self.run_js(r"""
const good = {schema_version:1, mode:'precomputed', source:{sha256:'a'.repeat(64)}, observations:[
{time:0, identity:'confirmed', signals:{orientation:null,body_motion:false,out_of_seat:false,hand_motion:false,posture_change:false}},
{time:2, identity:'uncertain', signals:{orientation:null,body_motion:null,out_of_seat:null,hand_motion:null,posture_change:null}}]};
const parsed = parseDataset(JSON.stringify(good));
assert.equal(sourceMatches(parsed, 'a'.repeat(64)), true);
assert.equal(sourceMatches(parsed, 'b'.repeat(64)), false);
assert.deepEqual(dueObservations(parsed.observations, 0, 1).map(x=>x.time), [0]);
for (const broken of [[], {...good,mode:'synthetic'}, {...good,schema_version:2}, {...good,source:{}},
 {...good,observations:[good.observations[1],good.observations[0]]},
 {...good,observations:[{...good.observations[0],time:-1}]},
 {...good,observations:[{...good.observations[0],identity:'lost'}]},
 {...good,observations:[{...good.observations[0],signals:{...good.observations[0].signals,extra:'invalid'}}]},
 {...good,observations:[{...good.observations[0],signals:{orientation:'false'}}]}]) {
 assert.throws(()=>parseDataset(JSON.stringify(broken)));
}
assert.throws(()=>parseDataset('{bad'));
assert.throws(()=>parseDataset(JSON.stringify(good).replace('\"values\":{}','\"values\":{\"x\":1e999}').replace('\"time\":0','\"time\":1e999')));
""")

    def test_context_binding_uses_exact_raw_tracking_and_context_hashes(self):
        self.run_js(r"""
(async()=>{
 const encoder=new TextEncoder();
 const source='a'.repeat(64);
 const tracking={schema_version:1,mode:'precomputed',source:{sha256:source},observations:[]};
 const trackingBytes=encoder.encode(JSON.stringify(tracking));
 const whitespaceChanged=encoder.encode(JSON.stringify(tracking)+'\n');
 const trackingHash=await sha256Hex(trackingBytes);
 assert.notEqual(trackingHash,await sha256Hex(whitespaceChanged));
 const context={schema_version:1,source_sha256:source,
   tracking_candidate_sha256:trackingHash,provenance:{},context_segments:[]};
 assert.equal(contextMatches(context,tracking,source,trackingHash),true);
 assert.equal(contextMatches(context,tracking,source,await sha256Hex(whitespaceChanged)),false);
 assert.equal(contextMatches(context,tracking,'b'.repeat(64),trackingHash),false);
 assert.equal(contextMatches({...context,source_sha256:'b'.repeat(64)},tracking,source,trackingHash),false);
 const firstContext=encoder.encode(JSON.stringify(context));
 const secondContext=encoder.encode(JSON.stringify(context)+' ');
 assert.notEqual(await sha256Hex(firstContext),await sha256Hex(secondContext));
})().catch(e=>{console.error(e);process.exitCode=1;});
""")

    def test_context_is_immutable_causal_and_uses_only_fixed_arabic_labels(self):
        self.run_js(r"""
const imported={provenance:{external_processing:true,requested_model:'never/render/provider prose'},
 context_segments:[
  {segment_id:'one',start_time:0,end_time:2,activity_suggestion:'table'},
  {segment_id:'two',start_time:2,end_time:4,activity_suggestion:'transition'}]};
const frozen=freezeContextDocument(imported);
assert.ok(Object.isFrozen(frozen));assert.ok(Object.isFrozen(frozen.provenance));
assert.ok(Object.isFrozen(frozen.context_segments));assert.ok(Object.isFrozen(frozen.context_segments[0]));
assert.equal(visibleContextSegment(frozen.context_segments,1.999),null);
assert.equal(visibleContextSegment(frozen.context_segments,2).segment_id,'one');
assert.equal(visibleContextSegment(frozen.context_segments,4).segment_id,'two');
assert.equal(visibleContextSegment(frozen.context_segments,1),null); // rewind derives from source data
assert.equal(CONTEXT_LABELS.activity_suggestion.table,'نشاط على الطاولة');
assert.equal(CONTEXT_LABELS.activity_suggestion.transition,'انتقال بين الأنشطة');
assert.equal(CONTEXT_LABELS.activity_status.not_observable,'غير قابل للرصد');
assert.equal(CONTEXT_LABELS.interaction.yes,'نعم');
assert.equal(CONTEXT_LABELS.interaction.ambiguous,'ملتبس');
assert.equal(CONTEXT_LABELS.review.confirmed,'مؤكد من المراجع');
const external=contextProvenanceDisclosure(frozen.provenance);
assert.equal(external.summary,'معالجة خارجية');
assert.ok(!JSON.stringify(external).includes('never/render/provider prose'));
assert.equal(contextProvenanceDisclosure({external_processing:false}).summary,'معالجة محلية');
""")

    def test_context_review_overlay_is_isolated_by_all_four_immutable_ids(self):
        self.run_js(r"""
+class MemoryStorage {
+ constructor(){this.values=new Map();}
+ getItem(key){return this.values.has(key)?this.values.get(key):null;}
+ setItem(key,value){this.values.set(key,String(value));}
+}
+const storage=new MemoryStorage(),source='a'.repeat(64),tracking='b'.repeat(64),context='c'.repeat(64);
+const imported=freezeContextDocument({segment_id:'seg-1',clinician_confirmation:'pending'});
+assert.equal(readContextReview(storage,source,tracking,context,'seg-1',imported.clinician_confirmation),'pending');
+writeContextReview(storage,source,tracking,context,'seg-1','confirmed');
+assert.equal(readContextReview(storage,source,tracking,context,'seg-1','pending'),'confirmed');
+assert.equal(readContextReview(storage,'d'.repeat(64),tracking,context,'seg-1','pending'),'pending');
+assert.equal(readContextReview(storage,source,'d'.repeat(64),context,'seg-1','pending'),'pending');
+assert.equal(readContextReview(storage,source,tracking,'d'.repeat(64),'seg-1','pending'),'pending');
+assert.equal(readContextReview(storage,source,tracking,context,'seg-2','pending'),'pending');
+assert.equal(imported.clinician_confirmation,'pending');
+assert.throws(()=>writeContextReview(storage,source,tracking,context,'seg-1','useful'));
+assert.ok(contextReviewKey(source,tracking,context,'seg-1').includes('seg-1'));
""".replace('+',''))


if __name__ == '__main__':
    unittest.main()
