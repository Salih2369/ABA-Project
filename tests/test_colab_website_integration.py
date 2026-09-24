"""Real HTTP + served dashboard JS integration. Synthetic inputs, NOT CV validation."""
import json
from pathlib import Path
import re
import subprocess
import threading
import unittest
from urllib.request import urlopen

from aba_demo.server import create_server


class ColabWebsiteIntegrationTests(unittest.TestCase):
    def test_served_import_hash_unknown_identity_and_causal_replay(self):
        server = create_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_address[1]}'
            with urlopen(base) as response:
                html = response.read().decode('utf-8')
            scripts = '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', html, re.S))
            token = re.search(r'name="aba-token" content="([^"]+)"', html)[1]
            code = 'const assert=require("node:assert/strict");\n' + scripts
            code += '\nconst base=' + json.dumps(base) + ', token=' + json.dumps(token) + ';\n'
            code += r'''
(async()=>{
// Deliberately synthetic byte fixture: tests file identity, NOT video decoding.
const bytes=Buffer.from('SYNTHETIC HTTP INTEGRATION ONLY - NOT VIDEO OR CV EVIDENCE');
const hash=require('node:crypto').createHash('sha256').update(bytes).digest('hex');
const rows=[0,1,2,3,4].map(time=>({time,identity:time===3?'uncertain':'confirmed',
 signals:Object.fromEntries(KEYS.map(k=>[k,k==='body_motion'])),values:{}}));
const fixture={schema_version:1,mode:'precomputed',source:{sha256:hash},
 provenance:'SYNTHETIC HTTP INTEGRATION ONLY - NOT MODEL INFERENCE',observations:rows};
const trackingBytes=Buffer.from(JSON.stringify(fixture));
const trackingHash=await sha256Hex(trackingBytes);
const dataset=parseDataset(trackingBytes.toString('utf8'));
assert.ok(sourceMatches(dataset,hash));
assert.equal(sourceMatches(dataset,'0'.repeat(64)),false);
assert.throws(()=>parseDataset(JSON.stringify({...fixture,schema_version:99})));
assert.throws(()=>parseDataset(JSON.stringify({...fixture,observations:[{...rows[0],signals:{body_motion:true}}]})));
let latest, calls=[];
const api=async(path,payload)=>{
 const response=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json','X-ABA-Token':token},body:JSON.stringify(payload)});
 const result=await response.json();assert.equal(response.status,200,JSON.stringify(result));
 calls.push([path,payload]);return result;
};
const player=new CausalPlayer(api,s=>{latest=s;},e=>{throw e;});
await player.configure(dataset.observations,'table');
await player.seek(2);
assert.equal(latest.indicators.body_motion.state,'active');
assert.equal(latest.events.length,1);
assert.equal(latest.events[0].evidence_time,2);
const atTwo=JSON.stringify(latest);
const context={schema_version:1,source_sha256:hash,tracking_candidate_sha256:trackingHash,
 provenance:{adapter:'local_transformers',source_sha256:hash,
  requested_model:'vendor/local-model',resolved_model:'vendor/local-model',
  endpoint_provider:null,external_processing:false,structured_outputs:false,
  data_collection:'local',zero_data_retention_required:false,sent_fields:[],
  consent:{required:false,granted:false,source_sha256:null}},
 context_segments:[{segment_id:'ctx-1',start_time:0,end_time:2,
  activity_suggestion:'table',activity_status:'supported',
  target_material_interaction:'yes',adult_target_interaction_visible:'ambiguous',
  evidence_times:[1],clinician_confirmation:'pending'}]};
const contextBytes=Buffer.from(JSON.stringify(context));
const contextHash=await sha256Hex(contextBytes);
const localFetch=(path,options)=>fetch(base+path,options);
await validateContextBytes(localFetch,token,contextBytes,10);
assert.equal(contextMatches(context,dataset,hash,trackingHash),true);
assert.equal(contextMatches(context,dataset,hash,await sha256Hex(Buffer.concat([trackingBytes,Buffer.from('\n')]))),false);
assert.equal(visibleContextSegment(context.context_segments,1.999),null);
assert.equal(visibleContextSegment(context.context_segments,2).segment_id,'ctx-1');
const invalidContext=Buffer.from(JSON.stringify(context).replace('"schema_version":1','"schema_version":1,"schema_version":1'));
await assert.rejects(validateContextBytes(localFetch,token,invalidContext,10));
assert.equal(JSON.stringify(latest),atTwo); // Invalid optional context never resets tracking replay.
const storage={values:new Map(),getItem(k){return this.values.get(k)??null;},setItem(k,v){this.values.set(k,String(v));}};
const activityCalls=calls.filter(([path])=>path==='/api/activity').length;
writeContextReview(storage,hash,trackingHash,contextHash,'ctx-1','confirmed');
assert.equal(readContextReview(storage,hash,trackingHash,contextHash,'ctx-1','pending'),'confirmed');
assert.equal(calls.filter(([path])=>path==='/api/activity').length,activityCalls);
assert.equal(context.context_segments[0].clinician_confirmation,'pending');
await player.seek(3);
assert.equal(latest.identity,'uncertain');
assert.equal(latest.alert,null);
for(const item of Object.values(latest.indicators)) {
 assert.equal(item.state,'unobservable');assert.equal(item.value,null);
}
assert.equal(latest.events.length,1); // Historical event retained, no new fabricated event.
await player.seek(0);
assert.equal(latest.events.length,0);
await player.seek(2);
assert.equal(JSON.stringify(latest),atTwo);
assert.equal(calls.some(([p,row])=>p==='/api/observe'&&row.time===4),false);
const invalid=await fetch(base+'/api/observe',{method:'POST',headers:{'Content-Type':'application/json','X-ABA-Token':token},body:JSON.stringify({...rows[4],identity:'unknown'})});
assert.equal(invalid.status,400);
console.log('PASS: served JS + real HTTP; matching SHA accepted; mismatch/schema rejected; uncertain identity unobservable; rewind deterministic; future rows not sent. SYNTHETIC ONLY, no CV accuracy claim.');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
            result = subprocess.run(['node', '-'], input=code, capture_output=True,
                                    text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            print(result.stdout.strip())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
