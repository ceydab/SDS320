import os
from dotenv import load_dotenv
import requests

load_dotenv()

VANTOR_TOKEN = os.environ.get("VANTOR_KEY")


def connect_vantor(TOKEN=VANTOR_TOKEN):
    response = requests.get("https://account.maxar.com/api-key/api/v2/api-key",
                            api_key=TOKEN)
    print(response.status_code)
connect_vantor()
def load_data():
    pass