from flask import Flask, request
import json
import os
import time
import gspread
import openai
import requests
import hashlib
import hmac
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

VERIFY_TOKEN = "robodranderson123"
MAX_COMENTARIOS_POR_HORA = 20
MAX_DIRECTS_POR_HORA = 40
INTERACOES_ANTES_CTA = 3
DELAY_ENTRE_RESPOSTAS = 10  # segundos (aumentado para evitar bloqueio)

respostas_enviadas = {"comentario": {}, "direct": {}}
interacoes_por_usuario = {}
comentarios_respondidos = set()

# Lê variáveis de ambiente
openai.api_key = os.environ["OPENAI_API_KEY"]
INSTAGRAM_TOKEN = os.environ["INSTAGRAM_TOKEN"]
APP_SECRET = os.environ["INSTAGRAM_APP_SECRET"]

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
gc = gspread.authorize(credentials)
sheet = gc.open("webhook_instagram_logs").sheet1

def ler_lista_exclusao():
    try:
        with open("excluir_usuarios.txt", "r") as f:
            return [linha.strip().lower() for linha in f if linha.strip()]
    except FileNotFoundError:
        return []

def classificar_sentimento(texto):
    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Classifique a seguinte mensagem como: positivo, neutro, negativo ou sensível."},
                {"role": "user", "content": texto}
            ],
            temperature=0.4,
            max_tokens=10
        )
        return response.choices[0].message.content.strip().lower()
    except Exception as e:
        print("Erro ao classificar sentimento:", e)
        return "neutro"

def gerar_resposta(texto, sentimento, tipo, interacoes):
    base = ""

    if "consulta" in texto.lower() or "atendimento" in texto.lower():
        base = (
            "Sou médico especialista em clínica médica (RQE 18790), com 13 anos de experiência. "
            "Ajudo pessoas que passaram por relacionamentos abusivos a se regularem emocionalmente "
            "e superarem sintomas físicos e psicológicos do trauma, como ansiedade, insônia, confusão mental e hipervigilância."
        )
    elif "não tenho dinheiro" in texto.lower() or "não posso pagar" in texto.lower():
        base = (
            "Entendo sua situação. Uma alternativa é o curso 'Quebrando as Algemas' com 50% de desconto usando o cupom **MQA50**. "
            "O acesso é por 1 ano e a renovação é automática (você pode cancelar na Hotmart a qualquer momento)."
        )
    elif sentimento == "sensível":
        base = "Recebi sua mensagem com atenção. O que você sente é real e merece cuidado. Se quiser conversar, estou aqui."
    elif sentimento == "negativo":
        base = "Entendo que esse momento esteja difícil. Se precisar de uma direção, posso te orientar com cuidado e respeito."
    elif sentimento == "positivo":
        base = "Obrigado pela sua mensagem! Se quiser entender melhor como posso te ajudar, posso te explicar com calma."
    elif sentimento == "neutro":
        base = "Li sua mensagem. Se quiser me contar mais, me chama no direct. Lá consigo te ouvir melhor com privacidade."

    if tipo == "direct" and interacoes >= INTERACOES_ANTES_CTA:
        base += (
            " Se quiser conversar com alguém da minha equipe, clique aqui: "
            "https://api.whatsapp.com/send?phone=5527996677672&text=Olá!%20Gostaria%20de%20mais%20informações%20sobre%20as%20consultas%20com%20o%20Dr.%20Anderson%20Contaifer"
        )

    if tipo == "comentario":
        base = base.replace("www.quebrandoasalgemas.com.br", "link da bio")

    if not base.strip():
        base = "Recebi sua mensagem. Se quiser conversar melhor, me chama no direct. Lá consigo te ouvir com mais privacidade."

    return base[:2200] if tipo == "comentario" else base[:1000]

def gerar_appsecret_proof(token, secret):
    return hmac.new(
        key=secret.encode('utf-8'),
        msg=token.encode('utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()

def enviar_resposta_instagram(tipo, username, resposta, comment_id=None):
    try:
        proof = gerar_appsecret_proof(INSTAGRAM_TOKEN, APP_SECRET)

        if tipo == "comentario" and comment_id:
            if comment_id in comentarios_respondidos:
                print(f"🚫 Comentário {comment_id} já foi respondido. Pulando...")
                return False
            url = f"https://graph.facebook.com/v19.0/{comment_id}/replies"
            payload = {
                "message": resposta,
                "access_token": INSTAGRAM_TOKEN,
                "appsecret_proof": proof
            }
            r = requests.post(url, data=payload)
            if r.status_code == 200:
                comentarios_respondidos.add(comment_id)

        elif tipo == "direct" and username:
            url = "https://graph.facebook.com/v19.0/me/messages"
            payload = {
                "messaging_type": "RESPONSE",
                "recipient": {"id": username},
                "message": {"text": resposta},
                "access_token": INSTAGRAM_TOKEN,
                "appsecret_proof": proof
            }
            r = requests.post(url, json=payload)
        else:
            print("⚠️ Tipo inválido ou dados faltando para enviar resposta.")
            return False

        print(f"📤 Status da resposta ({tipo}):", r.status_code, r.text)

        if r.status_code != 200:
            print(f"❌ Falha ao enviar {tipo.upper()}: {r.status_code} - {r.text}")
            return False

        print(f"📤 {tipo.capitalize()} enviado com sucesso: {r.status_code}")
        return True

    except Exception as e:
        print("❌ Erro ao enviar resposta:", e)
        return False

