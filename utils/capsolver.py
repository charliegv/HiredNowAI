import requests
import time
from typing import Optional, Dict
from dotenv import load_dotenv
import os

load_dotenv()


class CapSolverClient:
    BASE_URL = "https://api.capsolver.com"

    def __init__(self, poll_interval: float = 1.5, timeout: int = 120):
        """
        :param api_key: Capsolver API key
        :param poll_interval: seconds to wait between polling result
        :param timeout: max seconds to wait for solution before giving up
        """
        self.api_key = os.getenv("CAPSOLVER_API_KEY")
        self.poll_interval = poll_interval
        self.timeout = timeout

    def create_task(self, task_params: Dict) -> Optional[str]:
        """Creates a Capsolver task and returns taskId."""
        payload = {
            "clientKey": self.api_key,
            "task": task_params
        }
        res = requests.post(f"{self.BASE_URL}/createTask", json=payload)
        resp = res.json()

        if resp.get("errorId", 0) != 0:
            print(f"[Capsolver] Failed to create task: {resp}")
            return None

        return resp.get("taskId")

    def get_result(self, task_id: str) -> Optional[Dict]:
        """Polls for the task result until ready or timeout."""
        start = time.time()

        while True:
            if time.time() - start > self.timeout:
                print("[Capsolver] Timeout waiting for result")
                return None

            payload = {"clientKey": self.api_key, "taskId": task_id}
            res = requests.post(f"{self.BASE_URL}/getTaskResult", json=payload)
            resp = res.json()

            if resp.get("status") == "ready":
                return resp.get("solution")

            if resp.get("status") == "failed" or resp.get("errorId", 0) != 0:
                print(f"[Capsolver] Solve failed: {resp}")
                return None

            time.sleep(self.poll_interval)

    def solve_turnstile(self, website_key: str, website_url: str, action: str = "") -> Optional[str]:
        """Convenience method to solve Cloudflare Turnstile."""
        task_params = {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteKey": website_key,
            "websiteURL": website_url,
            "metadata": {"action": action} if action else {}
        }

        task_id = self.create_task(task_params)
        if not task_id:
            return None

        solution = self.get_result(task_id)
        if not solution:
            return None

        return solution.get("token")
