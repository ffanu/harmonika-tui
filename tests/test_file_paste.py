import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime/tools'))
import file_paste as p
import workbench


class FilePasteTests(unittest.TestCase):
    def test_file_precedes_image_or_text_and_quotes_multiple_paths(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'foto café (1).png'; a.touch()
            b=Path(d)/'$(touch hacked).jpg'; b.touch()
            text=p.paste_text({'paths':[a.as_uri(),str(b),str(a)],'text':'image label'})
            import shlex
            self.assertEqual(shlex.split(text),[str(a),str(b)])
            self.assertNotIn('image label',text)

    def test_text_preserves_code_and_unicode(self):
        self.assertEqual(p.paste_text({'text':'a ✓\r\n    b\t1'}),'a ✓\n    b\t1')

    def test_image_without_origin_does_not_invent_or_upload(self):
        with self.assertRaisesRegex(ValueError,'no_file_or_text'):
            p.paste_text({'paths':[],'text':None})

    def test_rejects_missing_remote_control_and_oversized(self):
        for value in [{'paths':['file://remote/tmp/test.png']}, {'paths':['/missing-fixture-file']},
                      {'paths':['file:///tmp/a%1Bb']}, {'text':'\x1b[201~\r'},
                      {'text':'x'*(p.MAX_BYTES+1)}, {'paths':['x']}, {'paths':[None]}]:
            with self.subTest(value_type=list(value)),self.assertRaises(ValueError):p.paste_text(value)

    def test_inject_bracketed_literal_stdin_and_delete_temporary_buffer(self):
        calls=[]
        def run(argv,**kw):
            calls.append((argv,kw))
            return SimpleNamespace(returncode=0,stdout=b'Chat|0||harmonika\n')
        p.paste_into_pane({'tmux':'tmux','socket':'fixture'},'%0',reader=lambda:'a\nb',run=run)
        self.assertEqual(calls[1][1]['input'],b'a\nb')
        self.assertIn('-p',calls[2][0]);self.assertIn('-d',calls[2][0])
        self.assertIn('delete-buffer',calls[-1][0])
        self.assertFalse(any('a\nb' in arg for argv,kw in calls for arg in argv))

    def test_never_inject_into_shell_or_wrong_pane(self):
        def run(*args,**kw):return SimpleNamespace(returncode=0,stdout=b'Chat|0||zsh\n')
        with self.assertRaisesRegex(ValueError,'chat_only'):
            p.paste_into_pane({'tmux':'tmux','socket':'fixture'},'%0',reader=lambda:self.fail('read'),run=run)
        with self.assertRaisesRegex(ValueError,'invalid_pane'):
            p.paste_into_pane({},'; command')

    def test_file_paths_bypass_native_image_paste_handler(self):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'photo.png';image.touch();calls=[]
            def run(argv,**kw):
                calls.append((argv,kw))
                return SimpleNamespace(returncode=0,stdout=b'Chat|0||harmonika\n')
            p.paste_into_pane({'tmux':'tmux','socket':'fixture'},'%0',
                             reader=lambda:{'paths':[str(image)]},run=run)
            argv=next(argv for argv,kw in calls if 'paste-buffer' in argv)
            self.assertNotIn('-p',argv)
            self.assertEqual(calls[1][1]['input'],("'"+str(image)+"'").encode())
            self.assertFalse(any(str(image) in str(argv) for argv,kw in calls))

    def test_shortcuts_route_to_path_helper_not_native_image_paste(self):
        cfg=workbench.tmux_configuration('/tmp/test/context.json')
        for key in ('C-v','F6'):
            line=next(x for x in cfg.splitlines() if x.startswith('bind-key -n '+key+' '))
            self.assertIn('paste-path',line);self.assertIn('#{pane_id}',line)

    @unittest.skipUnless(sys.platform=='darwin','macOS clipboard protocol')
    def test_real_macos_private_pasteboard_file_urls(self):
        # Named pasteboard only: never replace the user's general clipboard.
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'photo café.png'; f.touch()
            name='harmonika-fixture-'+uuid.uuid4().hex
            script=p.READ_JXA.replace('$.NSPasteboard.generalPasteboard',
                                      '$.NSPasteboard.pasteboardWithName('+json.dumps(name)+')')
            setup='''
pb.clearContents;
const item=$.NSPasteboardItem.alloc.init;
const original=$.NSURL.URLWithString(%s);
item.setStringForType(original.fileReferenceURL.absoluteString, 'public.file-url');
item.setStringForType('fixture label', 'public.utf8-plain-text');
const itemsToWrite=$.NSMutableArray.alloc.init;
itemsToWrite.addObject(item);
pb.writeObjects(itemsToWrite);
''' % json.dumps(f.as_uri())
            script=script.replace('const paths = [];',setup+'\nconst paths = [];')
            result=subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',script],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            import shlex
            self.assertEqual([Path(x).resolve() for x in shlex.split(p.paste_text(json.loads(result.stdout)))],[f.resolve()])


if __name__=='__main__':unittest.main()
