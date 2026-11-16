"""JSON field selection and a shallow, readable field catalogue."""
import json
import re


def child_pointer(pointer, key):
    return pointer + '/' + str(key).replace('~', '~0').replace('/', '~1')


def shape(value, pointer, depth=1):
    kind = {dict:'object', list:'array', str:'string', int:'integer',
            float:'number', bool:'boolean', type(None):'null'}[type(value)]
    result = {'pointer':pointer, 'type':kind}
    if isinstance(value, dict):
        result.update(size=len(value), keys=list(value))
    elif isinstance(value, list):
        result['size'] = len(value)
        if depth:
            result['items'] = [shape(item, child_pointer(pointer, i), depth-1)
                               for i,item in enumerate(value[:3])]
    else:
        result['preview'] = value[:120] if isinstance(value, str) else value
    return result


class JsonPointerLookupError(ValueError):
    """Keep field coordinates typed across the tool/public-diagnostic boundary."""
    def __init__(self, pointer, current, value):
        node = shape(value, current)
        self.diagnostic = {'pointer': pointer, 'current': current,
                           **{key: node[key] for key in ('type', 'size', 'keys') if key in node}}
        super().__init__(f'JSON 路径 {pointer!r} 不存在；当前节点 {current!r}：'
                         + json.dumps(node, ensure_ascii=False)[:2000])


def select(value, pointer):
    if pointer and not pointer.startswith('/'):
        raise ValueError('json_pointer 使用 /字段/数组下标，例如 /q2/0/optimum；空字符串表示根节点')
    current = ''
    for token in pointer.split('/')[1:]:
        if re.search(r'~(?![01])', token):
            raise ValueError('JSON Pointer 中 ~ 写作 ~0，字段名中的 / 写作 ~1')
        key = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', key) and int(key) < len(value):
            value = value[int(key)]
        else:
            raise JsonPointerLookupError(pointer, current, value)
        current = child_pointer(current, key)
    return value


def render(text, pointer, view):
    value = select(json.loads(text), pointer)
    if view == 'structure':
        summary = shape(value, pointer)
        if isinstance(value, dict):
            summary.pop('keys')
            summary['fields'] = {key:shape(item, child_pointer(pointer, key)) for key,item in value.items()}
        value = summary
    return json.dumps(value, ensure_ascii=False, indent=2)
