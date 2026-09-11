import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

from filedrop.handler import Handler
from filedrop.paths import cleanup_temporary_folder, create_temporary_folder
from filedrop.server import FileDropHTTPServer
from filedrop.state import ServerState


class FileDropServerSmokeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="filedrop-test-")
        ServerState.upload_folder = self.folder
        ServerState.pin = None
        self.httpd = FileDropHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:{}".format(self.httpd.server_port)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        cleanup_temporary_folder(self.folder)

    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        return urllib.request.urlopen(req, timeout=5)

    def test_upload_list_and_download_unicode_file(self):
        boundary = "----filedrop-test"
        payload = "Привет, FileDrop!\n".encode("utf-8")
        body = (
            ("--{}\r\n".format(boundary)
             + 'Content-Disposition: form-data; name="files"; filename="фото.txt"\r\n'
             + "Content-Type: text/plain; charset=utf-8\r\n\r\n").encode("utf-8")
            + payload
            + ("\r\n--{}--\r\n".format(boundary)).encode("ascii")
        )
        with self.request(
            "/upload",
            data=body,
            headers={
                "Content-Type": "multipart/form-data; boundary={}".format(boundary),
                "Content-Length": str(len(body)),
            },
        ) as response:
            result = json.load(response)
        self.assertTrue(result["ok"])
        saved_name = result["files"][0]["name"]
        self.assertEqual(Path(self.folder, saved_name).read_bytes(), payload)

        with self.request("/api/files") as response:
            listed = json.load(response)["files"]
        self.assertEqual([item["name"] for item in listed], [saved_name])

        query = urllib.parse.urlencode({"name": saved_name})
        with self.request("/dl?{}".format(query)) as response:
            self.assertEqual(response.read(), payload)

    def test_temporary_folder_is_removed(self):
        folder = create_temporary_folder()
        Path(folder, "probe.txt").write_text("probe", encoding="utf-8")
        cleanup_temporary_folder(folder)
        self.assertFalse(Path(folder).exists())


if __name__ == "__main__":
    unittest.main()
