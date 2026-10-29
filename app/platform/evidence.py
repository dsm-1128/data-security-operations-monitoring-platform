import hashlib
import json
import os
from pathlib import Path
import re
import uuid


class EvidenceArchive:
    def __init__(self, directory='data/evidence', bucket='', endpoint=''):
        self.directory = Path(directory)
        self.bucket = bucket
        if bucket:
            import boto3
            from botocore.config import Config
            self.client = boto3.client('s3', endpoint_url=endpoint or None, config=Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 2}))

    def put(self, run_id, evidence):
        if not re.fullmatch(r'RUN-[0-9a-f]{32}', run_id) or not re.fullmatch(r'EV-[0-9a-f]{32}', evidence['id']):
            raise ValueError('无效证据标识')
        payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()
        if len(payload) > 128000:
            raise ValueError('证据归档超过限制')
        key = run_id + '/' + evidence['id'] + '.json'
        digest = hashlib.sha256(payload).hexdigest()
        if self.bucket:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=payload, ContentType='application/json', ServerSideEncryption='AES256', Metadata={'sha256': digest})
        else:
            target = self.directory / key
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            temporary.write_bytes(payload)
            os.replace(temporary, target)
        return {'key': key, 'sha256': digest, 'backend': 's3' if self.bucket else 'local'}

    def get(self, reference):
        key = reference['key']
        if not re.fullmatch(r'RUN-[0-9a-f]{32}/EV-[0-9a-f]{32}\.json', key):
            raise ValueError('无效归档路径')
        if reference['backend'] != ('s3' if self.bucket else 'local'):
            raise ValueError('归档配置已变化')
        if self.bucket:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            try:
                payload = response['Body'].read(128001)
            finally:
                response['Body'].close()
        else:
            with (self.directory / key).open('rb') as stream:
                payload = stream.read(128001)
        if len(payload) > 128000 or hashlib.sha256(payload).hexdigest() != reference['sha256']:
            raise ValueError('归档完整性校验失败')
        return json.loads(payload)
