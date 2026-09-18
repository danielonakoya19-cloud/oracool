"""Offline regression tests: no credentials, network calls or server import."""
import ast
import html
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / 'server.py').read_text())
names = {'_decode_agent_angles', '_strip_agent_markup', '_markup_commands', '_MarkupGuard'}
nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
ns = {'re': re, '_AGENT_BLOCK_TAGS': ('tool_calls', 'tool_call', 'function_calls', 'invoke', 'tool_use', 'antml:invoke', 'antml:function_calls')}
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<markup>', 'exec'), ns)

BLOCK = ('<tool_call><arg_key>command</arg_key><arg_value>market lookup ticker DANGCEM NGX</arg_value>'
         '<arg_key>intent</arg_key><arg_value>invest_dangote_lookup</arg_value>'
         '<arg_key>user_id</arg_key><arg_value>someone@example.com</arg_value>'
         '<arg_key>tools</arg_key><arg_value>[{"type":"function"}]</arg_value></invoke>')
VARIANTS = [BLOCK, html.escape(BLOCK), html.escape(html.escape(BLOCK)),
            BLOCK.replace('<', '&#60;').replace('>', '&#62;'),
            BLOCK.replace('<', '&#x003c;').replace('>', '&#x003e;')]

class MarkupTests(unittest.TestCase):
    def test_whole_reply(self):
        for block in VARIANTS:
            self.assertEqual(ns['_strip_agent_markup']('Before. '+block), 'Before.')
    def test_metadata_is_not_command(self):
        for block in VARIANTS:
            self.assertEqual(ns['_markup_commands'](block), ['market lookup ticker DANGCEM NGX'])
    def test_every_chunk_width(self):
        for block in VARIANTS:
            text = 'Before. '+block+' After.'
            for width in (1,2,3,7,27,28,64,127,128,129,512,len(text)):
                guard = ns['_MarkupGuard']()
                parts = []
                for i in range(0,len(text),width):
                    parts.extend(guard.feed(text[i:i+width]))
                parts.append(guard.tail())
                self.assertEqual(''.join(parts), 'Before.  After.', (width,block[:20]))
                self.assertEqual(ns['_markup_commands'](guard.captured), ['market lookup ticker DANGCEM NGX'])
    def test_every_two_chunk_split(self):
        for block in VARIANTS:
            text = 'Before. '+block+' After.'
            for i in range(len(text)+1):
                g=ns['_MarkupGuard']()
                self.assertEqual(''.join(g.feed(text[:i])+g.feed(text[i:])+[g.tail()]), 'Before.  After.')
    def test_plain_prose_spacing(self):
        text = 'This is ordinary text. ' * 100
        g=ns['_MarkupGuard'](); chunks=[]
        for c in text:
            chunks.extend(g.feed(c))
        self.assertEqual(''.join(chunks)+g.tail(),text)
    def test_incomplete_block(self):
        for block in VARIANTS:
            g=ns['_MarkupGuard']()
            self.assertEqual(''.join(g.feed('Before. '+block[:-50]))+g.tail(), 'Before. ')
    def test_multiple_blocks(self):
        g=ns['_MarkupGuard']()
        self.assertEqual(''.join(g.feed('A '+BLOCK+' B '+html.escape(BLOCK)+' C'))+g.tail(),'A  B  C')

if __name__ == '__main__':
    unittest.main()
