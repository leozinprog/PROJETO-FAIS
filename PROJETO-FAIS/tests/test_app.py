import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app


class AppReceiptFlowTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_receipt_upload_triggers_automatic_confirmation(self):
        comprovante = io.BytesIO(
            b"Aluno: Maria\nMatricula: 100\nValor: R$ 150,00\nData: 10/09/2026\n"
        )
        comprovante.name = "comprovante.txt"

        response = self.client.post(
            "/analisar",
            data={
                "comprovante": (comprovante, "comprovante.txt"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["result"][0]["action"], "BAIXA AUTOMÁTICA")
        self.assertTrue(payload["result"][0]["auto_baixa"])

        history = self.client.get('/baixas')
        self.assertEqual(history.status_code, 200)
        self.assertGreater(len(history.get_json()['baixas']), 0)


if __name__ == "__main__":
    unittest.main()
