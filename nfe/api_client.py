import requests
from django.conf import settings

API_BASE_URL = "https://api.meudanfe.com.br/v2/fd"
HEADERS = {
    "Api-Key": settings.API_KEY,
    "accept": "application/json"
}

def add_chave(chave):
    url = f"{API_BASE_URL}/add/{chave}"
    resp = requests.put(url, headers=HEADERS)
    if resp.status_code == 200:
        return resp.json()
    else:
        raise Exception(f"Erro {resp.status_code}: {resp.text}")

def baixar_pdf(chave):
    url = f"{API_BASE_URL}/get/da/{chave}"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        return resp.json()
    else:
        return None

def baixar_xml(chave):
    url = f"{API_BASE_URL}/get/xml/{chave}"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        return resp.json()
    else:
        return None