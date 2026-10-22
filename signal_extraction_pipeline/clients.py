import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


def post(url, payload, api_key=None, timeout=120):
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    for attempt in range(3):
        try:
            request = Request(url, data=json.dumps(payload).encode(), headers=headers)
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f'API returned HTTP {error.code}: {urlsplit(url).path}') from error
            time.sleep(2 ** attempt)
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


class ChatClient:
    def __init__(self, base_url, model, context_window, max_output_tokens, api_key=None,
                 tokenize_url=None, timeout=120):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens
        self.api_key = api_key
        self.timeout = timeout
        parsed = urlsplit(self.base_url)
        self.tokenize_url = tokenize_url or urlunsplit((parsed.scheme, parsed.netloc,
                                                       parsed.path.removesuffix('/v1') + '/tokenize', '', ''))

    def count(self, messages):
        result = post(self.tokenize_url, {'model': self.model, 'messages': messages,
                                         'add_generation_prompt': True}, self.api_key, self.timeout)
        count = result['count']
        if type(count) is not int or count < 0:
            raise ValueError('Tokenizer returned an invalid count')
        maximum = result.get('max_model_len', self.context_window)
        if type(maximum) is not int or maximum <= 0:
            raise ValueError('Tokenizer returned an invalid max_model_len')
        budget = min(maximum, self.context_window) - self.max_output_tokens
        return count, budget

    def fits(self, messages):
        count, budget = self.count(messages)
        return count <= budget

    def complete(self, messages):
        if not self.fits(messages):
            raise ValueError('LLM input plus reserved output exceeds the context window; no text was truncated')
        result = post(self.base_url + '/chat/completions', {
            'model': self.model, 'messages': messages, 'temperature': 0,
            'max_tokens': self.max_output_tokens,
        }, self.api_key, self.timeout)
        choice = result['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError(f"LLM did not finish normally: {choice.get('finish_reason')}")
        text = choice['message']['content']
        if not isinstance(text, str) or not text.strip():
            raise ValueError('LLM returned no text')
        return text


class EmbeddingClient:
    def __init__(self, base_url, model, api_key=None, timeout=120):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def embed(self, texts):
        result = post(self.base_url + '/embeddings', {'model': self.model, 'input': texts,
                                                     'encoding_format': 'float'}, self.api_key, self.timeout)
        data = result['data']
        indices = [item['index'] for item in data]
        if sorted(indices) != list(range(len(texts))):
            raise ValueError('Embedding response indices do not match inputs')
        vectors = [item['embedding'] for item in sorted(data, key=lambda item: item['index'])]
        for vector in vectors:
            if (not vector or any(isinstance(x, bool) or not isinstance(x, (int, float))
                                  or not math.isfinite(x) for x in vector)):
                raise ValueError('Invalid embedding vector')
        return vectors
