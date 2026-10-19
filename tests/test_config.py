import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
SPEC = importlib.util.spec_from_file_location('config', ROOT / 'lib/config.py')
config = importlib.util.module_from_spec(SPEC) if SPEC else None
if SPEC and SPEC.loader and (ROOT / 'lib/config.py').exists():
    SPEC.loader.exec_module(config)


def profile(**changes):
    p = dict(domain='app.example.com', origin_ip='8.8.8.8', app='nextjs',
             host='127.0.0.1', port=3107, protocol='http')
    p.update(changes)
    return p


class ConfigTests(unittest.TestCase):
    def test_valid_profile_gets_bounded_defaults(self):
        p = config.validate(profile())
        self.assertEqual(p['buffer'], 'auth')
        self.assertEqual(p['timeout'], 60)
        self.assertFalse(p['https'])

    def test_rejects_injection_and_invalid_boundaries(self):
        for changes in [dict(domain='a.com;return 200;'), dict(domain='*.example.com'),
                        dict(host='foo; include /tmp/x;'), dict(port=0), dict(port=65536),
                        dict(port=True), dict(timeout=-1), dict(root='/x;return 200'),
                        dict(api_prefix='/api/ {'), dict(origin_ip='127.0.0.1'),
                        dict(https='false'), dict(app=['nextjs']), dict(origin_ip={}), dict(token='secret'), dict(domain='-a.example.com')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                config.validate(profile(**changes))

    def test_static_and_api_render_preserves_prefix_and_header_buffer(self):
        p = config.validate(profile(app='react', root='/srv/www/app', api=True,
                                    api_host='127.0.0.1', api_port=5000, streaming=True,
                                    websocket=True))
        text = config.render(p, '/var/lib/nginx-manager/acme', '', '')
        self.assertIn('try_files $uri $uri/ /index.html;', text)
        self.assertIn('location /api/ {', text)
        self.assertIn('proxy_pass http://127.0.0.1:5000;', text)
        self.assertIn('proxy_buffering off;', text)
        self.assertIn('proxy_buffer_size 32k;', text)
        self.assertNotIn('Connection "upgrade"', text)
        self.assertIn('map $http_upgrade', text)
        self.assertNotIn('proxy_add_x_forwarded_for', text)

    def test_https_never_references_missing_certificate(self):
        p = config.validate(profile(https=True))
        with self.assertRaises(ValueError):
            config.render(p, '/var/lib/nginx-manager/acme', '', '')
        with tempfile.TemporaryDirectory() as d:
            cert, key = Path(d, 'cert'), Path(d, 'key')
            cert.touch(); key.touch()
            text = config.render(p, '/var/lib/nginx-manager/acme', str(cert), str(key))
            self.assertIn('listen 443 ssl;', text)
            self.assertNotIn('@@', text)
            self.assertIn('return 301 https://$host$request_uri;', text)
            self.assertIn('location ^~ /.well-known/acme-challenge/', text)

    def test_parser_inserts_include_only_inside_http(self):
        main = '/etc/nginx/nginx.conf'
        original = 'events {}\nhttp { # } fake\n server { return 200 "}"; }\n}\n'
        dump = '# configuration file ' + main + ':\n' + original
        layout = config.layout(dump, main, '/etc/nginx/nginx-manager.d')
        self.assertEqual(layout['file'], main)
        self.assertIn('http {\n    include /etc/nginx/nginx-manager.d/*.conf;', layout['content'])
        self.assertTrue(layout['changed'])
        self.assertIn('return 200 "}";', layout['content'])

    def test_parser_reuses_only_http_context_glob(self):
        main = '/etc/nginx/nginx.conf'
        dump = f'# configuration file {main}:\ninclude /etc/nginx/conf.d/*.conf;\nevents {{}}\nhttp {{ include /etc/nginx/sites/*.conf; }}\n'
        result = config.layout(dump, main, '/etc/nginx/nginx-manager.d')
        self.assertTrue(result['changed'])
        dump = f'# configuration file {main}:\nevents {{}} http {{ include /etc/nginx/conf.d/*.conf; }}\n'
        self.assertFalse(config.layout(dump, main, '/etc/nginx/nginx-manager.d')['changed'])

    def test_domain_conflicts_cover_manual_alias_wildcard_and_regex(self):
        dump = '# configuration file /etc/nginx/nginx.conf:\nhttp { server { server_name example.com *.example.net .example.org; } }'
        for name in ['example.com', 'a.example.net', 'example.org', 'a.example.org']:
            self.assertTrue(config.conflicts(dump, name))
        self.assertFalse(config.conflicts(dump, 'fresh.example.com'))
        self.assertTrue(config.conflicts(dump + '\n# configuration file /etc/nginx/r.conf:\nserver { server_name ~^app; }', 'app.other.com'))

    def test_header_and_log_summary_never_returns_values(self):
        out = config.header_summary('HTTP/1.1 200 OK\r\nSet-Cookie: access_token=TOPSECRET\r\nSet-Cookie: refresh_token=ALSOSECRET\r\nX-Other: value\r\n\r\n')
        self.assertIn('set-cookie: count=2', out)
        self.assertNotIn('TOPSECRET', out)
        self.assertNotIn('ALSOSECRET', out)
        log = config.log_summary('upstream sent too big header cookie=TOPSECRET\nrequest /?token=SECRET\nconnect() failed (111: Connection refused)')
        self.assertIn('oversized upstream response headers', log)
        self.assertNotIn('SECRET', log)

    def test_profiles_are_data_and_unknown_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, 'profile.conf')
            f.write_text('DOMAIN=$(touch /tmp/nope)')
            with self.assertRaises(ValueError):
                config.load(f)
            f.write_text(json.dumps(profile(secret='no')))
            with self.assertRaises(ValueError):
                config.load(f)


    def test_ipv6_origin_gets_ipv6_listener(self):
        text=config.render(config.validate(profile(origin_ip='2606:4700:4700::1111')), '/var/lib/nginx-manager-acme')
        self.assertIn('listen [::]:80;',text)


if __name__ == '__main__':
    unittest.main()
