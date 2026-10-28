"""Read DEX declarations/annotations without executing Android code."""
import argparse
import json
import re
import struct
import zipfile


class Dex:
    def __init__(self, data):
        self.data = data
        count, offset = self.pair(56)
        self.strings = []
        for i in range(count):
            pos = self.u32(offset + i * 4)
            _, pos = self.uleb(pos)
            end = data.index(0, pos)
            self.strings.append(data[pos:end].decode('utf-8', 'replace'))
        count, offset = self.pair(64)
        self.types = [self.strings[self.u32(offset + i * 4)] for i in range(count)]
        count, offset = self.pair(72)
        self.protos = []
        for i in range(count):
            _, ret, params = struct.unpack_from('<III', data, offset + i * 12)
            args = [] if not params else [self.types[self.u16(params + 4 + j * 2)] for j in range(self.u32(params))]
            self.protos.append({'args': args, 'return': self.types[ret]})
        count, offset = self.pair(80)
        self.fields = []
        for i in range(count):
            cls, typ, name = struct.unpack_from('<HHI', data, offset + i * 8)
            self.fields.append({'class': self.types[cls], 'name': self.strings[name], 'type': self.types[typ]})
        count, offset = self.pair(88)
        self.methods = []
        for i in range(count):
            cls, proto, name = struct.unpack_from('<HHI', data, offset + i * 8)
            self.methods.append({'class': self.types[cls], 'name': self.strings[name], **self.protos[proto]})
        count, offset = self.pair(96)
        self.classes = []
        for i in range(count):
            c = struct.unpack_from('<IIIIIIII', data, offset + i * 32)
            self.classes.append({'class': self.types[c[0]], 'annotations_offset': c[5], 'data_offset': c[6]})

    def pair(self, pos): return struct.unpack_from('<II', self.data, pos)
    def u32(self, pos): return struct.unpack_from('<I', self.data, pos)[0]
    def u16(self, pos): return struct.unpack_from('<H', self.data, pos)[0]

    def uleb(self, pos):
        value = shift = 0
        while True:
            b = self.data[pos]
            pos += 1
            value |= (b & 127) << shift
            if not b & 128: return value, pos
            shift += 7

    def value(self, pos):
        header = self.data[pos]
        pos += 1
        typ, arg = header & 31, header >> 5
        if typ == 28:
            size, pos = self.uleb(pos)
            result = []
            for _ in range(size):
                value, pos = self.value(pos)
                result.append(value)
            return result, pos
        if typ == 29: return self.annotation(pos)
        if typ == 30: return None, pos
        if typ == 31: return bool(arg), pos
        n = int.from_bytes(self.data[pos:pos + arg + 1], 'little')
        pos += arg + 1
        if typ == 23: n = self.strings[n]
        elif typ == 24: n = self.types[n]
        elif typ in (25, 27): n = self.fields[n]
        elif typ == 26: n = self.methods[n]
        return n, pos

    def annotation(self, pos):
        typ, pos = self.uleb(pos)
        size, pos = self.uleb(pos)
        result = {'annotation': self.types[typ]}
        for _ in range(size):
            name, pos = self.uleb(pos)
            result[self.strings[name]], pos = self.value(pos)
        return result, pos

    def annotation_set(self, pos):
        if not pos: return []
        return [self.annotation(self.u32(pos + 4 + 4 * i) + 1)[0] for i in range(self.u32(pos))]

    def declared(self, cls):
        off = cls['annotations_offset']
        annotated = {}
        class_annotations = []
        if off:
            co, fc, mc, pc = struct.unpack_from('<IIII', self.data, off)
            class_annotations = self.annotation_set(co)
            for i in range(mc):
                idx, ao = self.pair(off + 16 + fc * 8 + i * 8)
                annotated[idx] = self.annotation_set(ao)
        pos = cls['data_offset']
        result = {**cls, 'annotations': class_annotations, 'fields': [], 'methods': []}
        if not pos: return result
        sizes = []
        for _ in range(4):
            n, pos = self.uleb(pos)
            sizes.append(n)
        for size in sizes[:2]:
            idx = 0
            for _ in range(size):
                delta, pos = self.uleb(pos)
                flags, pos = self.uleb(pos)
                idx += delta
                result['fields'].append({**self.fields[idx], 'flags': flags})
        for size in sizes[2:]:
            idx = 0
            for _ in range(size):
                delta, pos = self.uleb(pos)
                flags, pos = self.uleb(pos)
                code, pos = self.uleb(pos)
                idx += delta
                result['methods'].append({**self.methods[idx], 'flags': flags, 'code_offset': code, 'annotations': annotated.get(idx, [])})
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('apk')
    parser.add_argument('--match', default='login|sms|coupon|rewards')
    parser.add_argument('--classes', action='store_true')
    args = parser.parse_args()
    with zipfile.ZipFile(args.apk) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r'classes\d*\.dex', name): continue
            dex = Dex(archive.read(name))
            for cls in dex.classes:
                item = dex.declared(cls)
                matches = [m for m in item['methods'] if re.search(args.match, json.dumps(m['annotations']), re.I)]
                if matches or (args.classes and re.search(args.match, cls['class'], re.I)):
                    if matches and not args.classes: item['methods'] = matches; item['fields'] = []
                    print(json.dumps({'dex': name, **item}, ensure_ascii=False))
