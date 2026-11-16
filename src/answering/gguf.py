"""Bounded GGUF metadata inspection; never loads weights or executes model code."""
import struct
import hashlib


def model_metadata(path):
    with open(path, 'rb') as stream:
        def unpack(fmt):
            return struct.unpack('<' + fmt, stream.read(struct.calcsize('<' + fmt)))
        def string():
            length, = unpack('Q')
            if length > 16 * 1024 * 1024:
                raise ValueError('invalid_gguf_metadata')
            raw = stream.read(length)
            if len(raw) != length:
                raise ValueError('invalid_gguf_metadata')
            return raw.decode('utf-8')
        def value(kind, array=False):
            if kind == 8:
                return string()
            if kind == 9 and not array:
                element, count = unpack('IQ')
                if count > 1_000_000:
                    raise ValueError('invalid_gguf_metadata')
                for _ in range(count):
                    value(element, array=True)
                return None
            fmt = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f',
                   7: '?', 10: 'Q', 11: 'q', 12: 'd'}.get(kind)
            if fmt is None:
                raise ValueError('invalid_gguf_metadata')
            return unpack(fmt)[0]
        try:
            magic, version, _, count = unpack('4sIQQ')
            if magic != b'GGUF' or version != 3 or count > 1024:
                raise ValueError('invalid_gguf_metadata')
            selected = {}
            wanted = {'general.architecture', 'general.name', 'general.size_label', 'general.file_type',
                      'qwen35.block_count', 'qwen35.embedding_length', 'qwen35.nextn_predict_layers'}
            for _ in range(count):
                key = string()
                item = value(unpack('I')[0])
                if key in wanted:
                    selected[key] = item
                if key == 'tokenizer.chat_template' and isinstance(item, str):
                    selected['chat_template_sha256'] = hashlib.sha256(item.encode('utf-8')).hexdigest()
            if (selected.get('general.architecture') != 'qwen35'
                    or selected.get('general.size_label') != '0.8B'
                    or selected.get('general.file_type') != 17
                    or selected.get('qwen35.nextn_predict_layers', 0) not in (0, 1)
                    or selected.get('qwen35.block_count', 0) - selected.get('qwen35.nextn_predict_layers', 0) != 24
                    or selected.get('qwen35.embedding_length') != 1024):
                raise ValueError('unexpected_answer_model')
            return selected
        except (struct.error, UnicodeError):
            raise ValueError('invalid_gguf_metadata') from None
