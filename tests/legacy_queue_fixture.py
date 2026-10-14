"""Test-only persisted v0.8 queue records, never a production creation bypass."""
import copy
import json
import uuid
from factory_queue import now


def accept_legacy_job(factory, engine_id, request, *, media=False, run_id=None):
    """Seed the accepted record format so retired runner behavior stays covered."""
    engine = next(item for item in factory.engines() if item['id'] == engine_id)
    batch_id = uuid.uuid4().hex
    obj = {'id': uuid.uuid4().hex, 'name': 'Legacy fixture', 'mainImageId': None, 'auxiliaryImages': []}
    job = factory._new_job(batch_id, obj, engine)
    job.update(kind=engine['kind'], inputMode=request.get('mode', 'model'), viewImages={}, request=copy.deepcopy(request))
    if media:
        job['mediaJob'] = True
    if run_id:
        job['runId'] = run_id
    factory.data['batches'].append({'id': batch_id, 'name': 'Accepted before scope change', 'kind': engine['kind'],
                                  'createdAt': now(), 'factoryVersion': '0.8.0a', 'objects': [],
                                  'engineIds': [engine_id], 'jobs': [job]})
    factory._save()
    request_path = factory.storage / f"{job['id']}-views.json"
    request_path.write_text(json.dumps(request), encoding='utf-8')
    return copy.deepcopy(job), request_path
