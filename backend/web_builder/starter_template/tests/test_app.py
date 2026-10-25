import os
import tempfile
import unittest
import importlib
from fastapi.testclient import TestClient

TMP = tempfile.TemporaryDirectory()
os.environ["APP_DATABASE"] = TMP.name + "/fixture.sqlite"
os.environ["APP_ADMIN_PASSWORD"] = "SyntheticFixturePasswordOnly"
os.environ["APP_SESSION_SECRET"] = "SyntheticFixtureSigningSecretOnly00000000"
app = importlib.import_module("app")


class Application(unittest.TestCase):
    def client(self):
        return TestClient(app.app, base_url="https://testserver")

    def login(self, c):
        r = c.post("/api/login", json={"password": os.environ["APP_ADMIN_PASSWORD"]})
        self.assertEqual(r.status_code, 200)
        return {"X-CSRF-Token": r.json()["csrf_token"]}

    def test_crud_and_real_database(self):
        with self.client() as c:
            self.assertEqual(c.get("/api/items").status_code, 401)
            headers = self.login(c)
            r = c.post(
                "/api/items",
                json={"name": "Synthetic Item", "quantity": 3},
                headers=headers,
            )
            self.assertEqual(r.status_code, 201)
            id = r.json()["id"]
            self.assertEqual(
                c.put(
                    "/api/items/" + str(id),
                    json={"name": "Changed Item", "quantity": 5},
                    headers=headers,
                ).status_code,
                200,
            )
            self.assertEqual(c.get("/api/items").json()["items"][0]["quantity"], 5)
            self.assertEqual(
                c.delete("/api/items/" + str(id), headers=headers).status_code, 200
            )
            self.assertEqual(c.get("/api/items").json()["items"], [])

    def test_csrf_origin_validation_and_cookie(self):
        with self.client() as c:
            headers = self.login(c)
            self.assertEqual(
                c.post("/api/items", json={"name": "x", "quantity": 1}).status_code, 403
            )
            self.assertEqual(
                c.post(
                    "/api/items",
                    json={"name": "x", "quantity": 1},
                    headers={**headers, "Origin": "https://evil.example"},
                ).status_code,
                403,
            )
            self.assertEqual(
                c.post(
                    "/api/items", json={"name": "x", "quantity": -1}, headers=headers
                ).status_code,
                422,
            )
            self.assertEqual(
                c.post(
                    "/api/items",
                    json={"name": "x", "quantity": 1, "unexpected": True},
                    headers=headers,
                ).status_code,
                422,
            )

    def test_logout_revokes_session(self):
        with self.client() as c:
            headers = self.login(c)
            self.assertEqual(c.post("/api/logout", headers=headers).status_code, 200)
            self.assertEqual(c.get("/api/items").status_code, 401)

    def test_public_page_has_no_fake_data_or_inline_scripts(self):
        with self.client() as c:
            r = c.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("script-src 'self'", r.headers["content-security-policy"])
            self.assertNotIn("Synthetic Item", r.text)


if __name__ == "__main__":
    unittest.main()
