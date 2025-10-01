#!/usr/bin/env python3
# monitor_nts_sefaz.py
"""
Monitora páginas (SEFAZ nacional + estaduais) procurando por "Nota Técnica" ou "Informe Técnico"
e envia um e-mail consolidado quando encontrar novos itens.

Estratégia Híbrida: Usa a busca por links (limpeza) e o RegEx agressivo (confiabilidade da data) 
para garantir que a NT de 29/09 e os Informes Técnicos sejam capturados e ordenados corretamente.
"""

import requests
import hashlib
import json
import os
import smtplib
import re
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from urllib.parse import urljoin

# ---------- Configurações de E-mail ----------
EMAIL_ORIGEM = "marina.lacerda@movetecnologia.com.br"
EMAIL_DESTINO = ["testessefazmove@gmail.com"] # pode adicionar mais: ["a@b.com","c@d.com"]
SENHA_EMAIL = os.getenv("SENHA_APP") # variável de ambiente
SMTP_SERVIDOR = "smtp.gmail.com"
SMTP_PORTA = 587

# ---------- URLs dos Portais ----------
URLS_PORTAIS = {
"Nota Técnica NF-e e NFC-e - Ambiente Nacional": "https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=04BIflQt1aY=",
"Informe Técnico NF-e e NFC-e - Ambiente Nacional": "https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=hXzemuyNHW4=",
"Nota Técnica MDFe - Ambiente Padrão e Nacional": "https://dfe-portal.svrs.rs.gov.br/Mdfe/Documentos",
"Nota Técnica CTe - Ambiente Padrão e Nacional": "https://www.cte.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=Y0nErnoZpsg=",
"Nota Técnica NFCom - MG": "https://portalsped.fazenda.mg.gov.br/spedmg/nfcom/Documentos/",
"Nota Técnica NF3e - MG": "https://portalsped.fazenda.mg.gov.br/spedmg/nf3e/Documentos/",
"Nota Técnica NF-e - MG": "https://portalsped.fazenda.mg.gov.br/spedmg/nfe/",
"Nota Técnica NFC-e - MG": "https://portalsped.fazenda.mg.gov.br/spedmg/nfce/", 
"Nota Técnica MDFe - MG": "https://portalsped.fazenda.mg.gov.br/spedmg/mdfe/",
"Nota Técnica CTe - MG": "https://portalsped.fazenda.gov.br/spedmg/cte/"
}

# ---------- Arquivos locais ----------
SEEN_HASHES_FILE = "seen_hashes.json"
USER_AGENT = "monitor-nt-sefaz/1.0 (+https://albatrosserp.com.br/)"

# ---------- Regex ----------
# Padrão geral para detectar NT/IT no texto do link
PATTERN = re.compile(r"(nota técnica|nota_tecnica|informe técnico|informe tecnico)", re.IGNORECASE)

# Padrão para extrair data do texto (usado para ordenação)
DATE_PATTERN = re.compile(r"(\d{2}/\d{2}/\d{4})", re.IGNORECASE)

# RegEx AGRESSIVO (usado como fallback para garantir dados difíceis como 29/09)
REGEX_DATA_TITULO_AGRESSIVO = re.compile(
    r"(Nota Técnica|Informe Técnico|Informe_tecnico|Nota_tecnica|MDFE_Nota_Tecnica).*?Publicada em (\d{2}/\d{2}/\d{4}).*?",
    re.IGNORECASE | re.DOTALL
)

HEADERS = {"User-Agent": USER_AGENT}

# ---------- Helpers ----------
def load_seen(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_seen(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def fetch_url(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[WARN] Erro ao buscar {url}: {e}")
        return None

def extract_date_from_text(text):
    match = DATE_PATTERN.search(text)
    if match:
        date_str = match.group(1)
        try:
            return datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            pass
    return None

def find_notes(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    dated_notes = {} # Usamos dict para garantir unicidade pelo hash

    # 1. Método Robust/Limpo (Busca por Links) - Prioriza URLs e Títulos corretos
    for a in soup.find_all("a", string=True):
        text = a.get_text(strip=True)
        
        if PATTERN.search(text):
            href = a.get("href") or ""
            full_url = urljoin(base_url, href)
            
            # Tenta encontrar a data para ordenação
            item_container = a.find_parent('tr') or a.find_parent(['li', 'div', 'p'])
            item_text = item_container.get_text(" ", strip=True) if item_container else text
            date_obj = extract_date_from_text(item_text)

            # A chave de hash aqui é TÍTULO + URL (o mais limpo possível)
            hash_key = sha256_text(f"{text}||{full_url}")

            if hash_key not in dated_notes:
                dated_notes[hash_key] = {
                    "title": text, 
                    "url": full_url,
                    "date": date_obj 
                }

    # 2. Método Agressivo (RegEx em texto simples) - Garante a detecção de datas difíceis (como 29/09)
    texto_simples = ' '.join(soup.get_text(" ", strip=True).split()) 
    ocorrencias = REGEX_DATA_TITULO_AGRESSIVO.finditer(texto_simples)

    for match in ocorrencias:
        titulo_completo = match.group(0).strip()
        data_publicacao_str = match.group(2)
        
        if 50 < len(titulo_completo) < 1000:
            date_obj = extract_date_from_text(data_publicacao_str)
            
            # A URL é o base_url, e o título é o bloco RegEx
            hash_key = sha256_text(f"{titulo_completo[:150]}||{base_url}")

            # Se esta nota já foi encontrada pelo método limpo, ignora a versão RegEx.
            # Se não foi encontrada, adiciona ou atualiza a entrada com a data.
            if hash_key not in dated_notes:
                 dated_notes[hash_key] = {
                    "title": titulo_completo,
                    "url": base_url,
                    "date": date_obj 
                }

    # 3. Ordenação: Data mais recente primeiro.
    # Converte o dicionário de volta para lista para ordenação
    final_list = list(dated_notes.values())
    
    final_list.sort(
        key=lambda x: x["date"] if x["date"] else datetime(1900, 1, 1), 
        reverse=True
    )
    
    # 4. Retorna a lista final de dicionários (title, url)
    return [{"title": n["title"], "url": n["url"]} for n in final_list]

def send_email(new_items_by_portal):
    msg = MIMEMultipart("alternative")
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    total_new_items = sum(len(items) for items in new_items_by_portal.values())
    
    # 🎨 Cores e Estilos
    PRIMARY_COLOR = "#900C3F" # Vinho/Bordô da marca
    HEADER_BG_COLOR = "#dddddd" # Cinza claro para o cabeçalho (Logo)
    SECONDARY_COLOR = "#495057" # Cinza escuro para o texto do rodapé
    BG_LIGHT = "#f7f7f7"       # Cinza mais claro e sutil para o fundo geral
    TABLE_HEADER_BG = PRIMARY_COLOR
    TABLE_ROW_BORDER = "#e9ecef" 
    LOGO_URL = "https://i.ibb.co/PvmtqJPF/LOGO-MOVE-PARA-FUNDO-CLARO.png" # Novo logo

    msg["Subject"] = f"🔔 [Alerta SEFAZ] {total_new_items} Novas Notas Técnicas / Informes"
    msg["From"] = EMAIL_ORIGEM
    msg["To"] = ", ".join(EMAIL_DESTINO)

    html_parts = []
    html_parts.append(f"""
    <html>
      <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: {BG_LIGHT}; margin: 0; padding: 0;">
        <div style="max-width: 650px; margin: 20px auto; background-color: #ffffff; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); overflow: hidden;">
            
            <div style="background-color: {HEADER_BG_COLOR}; padding: 20px 0; text-align: center;">
                <img src="{LOGO_URL}" alt="Albatross Logo" width="220" style="display: block; margin: 0 auto;">
            </div>

            <div style="padding: 25px 30px;">
                <h1 style="font-size: 22px; color: #333; margin-top: 0; font-weight: 600;">Atualização de Notas Técnicas SEFAZ</h1>
                <p style="font-size: 16px; line-height: 1.6; color: #555;">
                    Nosso monitor detectou <b style="color: {PRIMARY_COLOR};">{total_new_items}</b> <b style="font-weight: 600;">novos documentos</b> de impacto fiscal.
                    Listamos os <b style="color: {PRIMARY_COLOR};">3 mais recentes por portal</b> para garantir a conformidade:
                </p>
                
                <table style="border-collapse: collapse; width: 100%; margin-top: 25px; border-radius: 6px; overflow: hidden; border: 1px solid {TABLE_ROW_BORDER};">
                  <thead>
                    <tr style="background-color: {TABLE_HEADER_BG}; color: white; font-weight: 600;">
                      <th style="padding: 12px 10px; text-align: left; width: 30%;">PORTAL</th>
                      <th style="padding: 12px 10px; text-align: left; width: 45%;">TÍTULO DA NOTA</th>
                      <th style="padding: 12px 10px; text-align: center; width: 25%;">ORIGEM</th>
                    </tr>
                  </thead>
                  <tbody>
    """)

    # Estilos da Tabela
    row_style_white = f"background-color: #ffffff; border-bottom: 1px solid {TABLE_ROW_BORDER};"
    row_style_light = f"background-color: {BG_LIGHT}; border-bottom: 1px solid {TABLE_ROW_BORDER};"
    cell_style = "padding: 12px 10px; vertical-align: middle; font-size: 14px; color: #333;"
    link_style = f"color: {PRIMARY_COLOR}; text-decoration: none; font-weight: 500;"
    
    i = 0
    # RegEx para limpar a data do título, se presente, para uma exibição mais limpa
    CLEANUP_PATTERN = re.compile(r"Publicada em \d{2}/\d{2}/\d{4}", re.IGNORECASE)

    for portal, items in new_items_by_portal.items():
        # Limite de 3 itens por portal
        for it in items[:3]: 
            raw_title = it["title"]
            title_clean = raw_title
            
            # 1. Tenta limpar qualquer "Publicada em DD/MM/AAAA" que possa ter sido capturado (útil para o RegEx agressivo)
            title_clean = CLEANUP_PATTERN.sub('', raw_title).strip()
            
            # 2. Se o título for muito longo (veio do RegEx agressivo), tenta remover o nome do portal e truncar.
            if len(title_clean) > 80:
                # Remove o nome completo do portal, que é o que está aparecendo repetidamente
                if title_clean.startswith(portal):
                    title_clean = title_clean[len(portal):].strip(' -:')
                if len(title_clean) > 80:
                   title_clean = title_clean[:77] + '...'
            
            # 3. Fallback se a limpeza falhar
            if not title_clean:
                 title_clean = raw_title # Mantém o título bruto como fallback final

            row_style = row_style_light if i % 2 == 0 else row_style_white
            i += 1
            
            html_parts.append(f"""
                <tr style="{row_style}">
                  <td style="{cell_style}">{portal}</td>
                  <td style="{cell_style}"><a href="{it['url']}" target="_blank" style="{link_style}">{title_clean}</a></td>
                  <td style="{cell_style} text-align: center;"><a href="{URLS_PORTAIS[portal]}" target="_blank" style="{link_style}; font-size: 13px;">🔗 Portal</a></td>
                </tr>
            """)
    
    html_parts.append(f"""
                  </tbody>
                </table>
                
                <div style="margin-top: 35px; text-align: center;">
                    <a href="{URLS_PORTAIS.get('Nota Técnica NF-e e NFC-e - Ambiente Nacional', '#')}" target="_blank" 
                       style="display: inline-block; padding: 12px 25px; background-color: {PRIMARY_COLOR}; color: white; 
                              text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                        Acessar Portal Principal da NF-e
                    </a>
                </div>
            </div>

            <div style="background-color: #f1f1f1; padding: 15px 30px; text-align: center; border-top: 1px solid {TABLE_ROW_BORDER};">
                <p style="color: {SECONDARY_COLOR}; font-size: 11px; margin: 0;">
                    Este e-mail é um serviço de monitoramento automático. Gerado em: {now}.
                </p>
            </div>
            
        </div>
      </body>
    </html>
    """)

    body_html = "".join(html_parts)
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        server = smtplib.SMTP(SMTP_SERVIDOR, SMTP_PORTA, timeout=30)
        server.ehlo()
        if SMTP_PORTA == 587:
            server.starttls()
            server.ehlo()
        server.login(EMAIL_ORIGEM, SENHA_EMAIL)
        server.sendmail(EMAIL_ORIGEM, EMAIL_DESTINO, msg.as_string())
        server.quit()
        print(f"[INFO] E-mail enviado para {EMAIL_DESTINO}")
    except Exception as e:
        print(f"[ERRO] Falha ao enviar e-mail: {e}")

# ---------- Main ----------
def main():
    print("Iniciado validador SEFAZ de Nota Técnica")
    if not SENHA_EMAIL:
        print("[ERRO] Variável de ambiente SENHA_APP não configurada. Configure a senha de aplicativo do Gmail para enviar e-mails.")

    seen = load_seen(SEEN_HASHES_FILE)
    new_found_by_portal = {} 
    updated_seen = seen.copy() 

    for portal, url in URLS_PORTAIS.items():
        print(f"[INFO] Verificando {portal}: {url}")
        html = fetch_url(url)
        if not html:
            continue
        
        # Estratégia Híbrida para extrair todos os itens e garantir a data mais recente
        notes = find_notes(html, url)
        
        portal_new_items = []
        
        for n in notes:
            # O hash usa o TÍTULO e a URL para unicidade.
            key_text = f"{n['title']}||{n['url']}"
            h = sha256_text(key_text)
            
            if h not in seen:
                updated_seen[h] = {
                    "title": n["title"],
                    "url": n["url"],
                    "portal": portal,
                    "first_seen": datetime.now().isoformat()
                }
                portal_new_items.append(n)

        if portal_new_items:
            print(f"[INFO] {len(portal_new_items)} novos itens detectados em {portal}.")
            new_found_by_portal[portal] = portal_new_items

    save_seen(SEEN_HASHES_FILE, updated_seen)

    total_new_items = sum(len(items) for items in new_found_by_portal.values())

    if total_new_items > 0:
        print(f"[INFO] Total de {total_new_items} novos itens detectados. Enviando e-mail...")
        send_email(new_found_by_portal)
    else:
        print("[INFO] Nenhuma nova Nota Técnica ou Informe Técnico encontrada.")

if __name__ == "__main__":
    main()