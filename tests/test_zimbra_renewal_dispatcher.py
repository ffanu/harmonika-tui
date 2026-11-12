from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.zimbra import renewal_dispatcher as d


class DispatcherTests(unittest.TestCase):
    def test_primary_is_not_sent_to_legacy_certbot_hooks(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory)
            for label in (d.PRIMARY,'other.example.test'):(root/(label+'.conf')).write_text('fixture')
            actual=Path.stat
            def owner(path,*args,**kwargs):
                info=actual(path,*args,**kwargs)
                class Meta:st_uid=0;st_mode=info.st_mode
                return Meta()
            # Path.is_file also needs regular mode only; no production config read.
            with patch.object(Path,'stat',owner):
                self.assertEqual(d.other_lineages(root),['other.example.test'])

    def test_other_lineages_disable_unsafe_global_deploy_and_force(self):
        args=d.argv('other.example.test')
        self.assertIn('--no-directory-hooks',args)
        self.assertEqual(args[args.index('--deploy-hook')+1],'/bin/true')
        self.assertNotIn('--force-renewal',args)
        self.assertEqual(args[args.index('--cert-name')+1],'other.example.test')


if __name__=='__main__':unittest.main()
