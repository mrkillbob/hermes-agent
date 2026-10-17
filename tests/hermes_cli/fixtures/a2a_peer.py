"""In-process JSON-RPC peer; no listener, account, process or model."""
import json
import threading


class Response:
    def __init__(self, body, url):
        self.data = json.dumps(body).encode()
        self.url, self.offset = url, 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def geturl(self):
        return self.url

    def read(self, size=-1):
        end = len(self.data) if size < 0 else self.offset + size
        value = self.data[self.offset:end]
        self.offset += len(value)
        return value

    read1 = read


class FakePeer:
    def __init__(self, states=('SUBMITTED', 'COMPLETED')):
        self.states, self.calls = list(states), []
        self.correlation = None
        self.lost_reply = self.forgotten = False
        self.change = lambda task: task
        self.entered, self.release = threading.Event(), None
        self.lock = threading.Lock()

    def open(self, request, timeout):
        body = json.loads(request.data)
        with self.lock:
            self.calls.append(body)
        if body['method'] == 'SendMessage':
            self.correlation = json.loads(body['params']['message']['parts'][0]['text'])['correlation']
            self.entered.set()
            if self.release is not None:
                assert self.release.wait(timeout=5)
            if self.lost_reply:
                raise TimeoutError('lost fake reply')
        else:
            assert body['method'] == 'GetTask'
            assert body['params']['id'] == 'peer-task'
            if self.forgotten:
                return Response({'jsonrpc': '2.0', 'id': body['id'], 'error': {'code': -32001, 'message': 'not found'}}, request.full_url)
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        task = {'id': 'peer-task', 'contextId': 'peer-context', 'status': {'state': 'TASK_STATE_' + state}}
        receipt = {'schema': 'hermes.a2a.receipt.v1', 'correlation': self.correlation,
                   'kind': 'error' if state == 'FAILED' else 'completed', 'summary': 'Synthetic result: 4',
                   'findings': [], 'questions': []}
        if state in {'COMPLETED', 'FAILED'}:
            task['artifacts'] = [{'artifactId': 'receipt-1', 'parts': [{'text': json.dumps(receipt)}]}]
        if state == 'INPUT_REQUIRED':
            receipt.update(kind='question', questions=['Which synthetic input?'])
            task['status']['message'] = {'parts': [{'text': json.dumps(receipt)}]}
        result = self.change(task)
        if body['method'] == 'SendMessage':
            result = {'task': result}
        return Response({'jsonrpc': '2.0', 'id': body['id'], 'result': result}, request.full_url)
