import json
import sqlite3
import pandas as pd
import requests
from flask import Flask, render_template, request, redirect, url_for, session
from requests import get
from requests.exceptions import ConnectionError
from urllib.parse import unquote
from re import search

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Oturum için gizli anahtar

# Trendyol API erişim bilgileri
AUTHORIZATION = 'Basic QlREbm5HcWtVdmVIOHRTbEdGQzQ6d3dEd2M0cFhmNEo1NjNOMXBKd3c='
USER_AGENT = '107703 - SelfIntegration'
SUPPLIER_ID = '107703'

# Trendyol API URL'si
base_url = f'https://api.trendyol.com/sapigw/suppliers/{SUPPLIER_ID}/products'

# HTTP isteği için gerekli başlıklar
headers = {
    'Authorization': AUTHORIZATION,
    'User-Agent': USER_AGENT
}


# Veritabanı bağlantısı ve tablo oluşturma
def init_db():
    with sqlite3.connect('database.db') as con:
        cur = con.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS products
                       (id INTEGER PRIMARY KEY, barcode TEXT UNIQUE, product_url TEXT, product_ids TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS CustomerId
                       (id INTEGER PRIMARY KEY, customer_id TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS logs
                       (id INTEGER PRIMARY KEY, timestamp TEXT, action TEXT, details TEXT)''')
        con.commit()


def log_action(action, details):
    with sqlite3.connect('database.db') as con:
        cur = con.cursor()
        cur.execute("INSERT INTO logs (timestamp, action, details) VALUES (datetime('now'), ?, ?)", (action, details))
        con.commit()


# Excel'den CustomerId tablosuna veri yükleme
def load_customer_ids_from_excel(file_path):
    df = pd.read_excel(file_path)
    with sqlite3.connect('database.db') as con:
        df.to_sql('CustomerId', con, if_exists='replace', index=False)


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        token = login(username, password)

        if token:
            session['token'] = token  # Token'i oturuma kaydet
            return redirect(url_for('products'))

    return render_template('index.html')


@app.route('/products', methods=['GET', 'POST'])
def products():
    token = session.get('token')  # Oturumdan token'i al
    if not token:
        return redirect(url_for('index'))

    query = request.args.get('query', '')
    page = 1
    all_products = []

    if request.method == 'POST':
        if 'file' in request.files:
            file = request.files['file']
            if file.filename != '':
                file_path = 'customer_ids.xlsx'
                file.save(file_path)
                load_customer_ids_from_excel(file_path)
                return redirect(url_for('products'))

        while True:
            response = fetch_products_page(token, page)
            if response.get('error'):
                return response['error']
            data = response['data']
            if not data:
                break

            for product in data:
                barcode = product['barcode']
                product_url = product['productUrl']
                product_ids = get_product_ids_by_barcode(token, barcode)
                all_products.append({
                    'barcode': barcode,
                    'product_url': product_url,
                    'product_ids': ','.join(product_ids) if product_ids else None
                })

                product_info = {
                    'barcode': barcode,
                    'product_url': product_url,
                    'product_ids': ','.join(product_ids) if product_ids else None
                }
                with sqlite3.connect('database.db') as con:
                    cur = con.cursor()
                    cur.execute("SELECT * FROM products WHERE barcode = ?", (barcode,))
                    existing_product = cur.fetchone()

                    if existing_product:
                        if (existing_product[2] != product_info['product_url'] or
                                existing_product[3] != product_info['product_ids']):
                            cur.execute("UPDATE products SET product_url = ?, product_ids = ? WHERE barcode = ?",
                                        (product_info['product_url'], product_info['product_ids'], barcode))
                            con.commit()
                    else:
                        cur.execute("INSERT INTO products (barcode, product_url, product_ids) VALUES (?, ?, ?)",
                                    (product_info['barcode'], product_info['product_url'], product_info['product_ids']))
                        con.commit()
            page += 1

    with sqlite3.connect('database.db') as con:
        cur = con.cursor()
        if query:
            cur.execute("SELECT * FROM products WHERE barcode LIKE ?",
                        ('%' + query + '%',))
        else:
            cur.execute("SELECT * FROM products")
        products_info = cur.fetchall()

    return render_template('products.html', products=products_info, query=query)



@app.route('/stop_fetching', methods=['POST'])
def stop_fetching():
    session['stop_fetching'] = True
    log_action("Stop Fetching", "Veri getirme işlemi durduruldu.")
    return redirect(url_for('products'))


def fetch_products_page(token, page, size=50, approved='True', on_sale='true'):
    if session.get('stop_fetching'):
        log_action("Fetching Stopped", f"Veri getirme işlemi sayfa {page} da durduruldu.")
        return {'error': 'Fetching stopped by user.'}

    params = {
        'page': page,
        'size': size,
        'approved': approved,
        'onSale': on_sale
    }
    response = requests.get(base_url, headers=headers, params=params)
    if response.status_code != 200:
        return {'error': f"Error: {response.status_code}, {response.text}"}

    data = response.json()
    products = data.get('content', [])
    return {'data': products}


@app.route('/products/<barcode>')
def product_detail(barcode):
    with sqlite3.connect('database.db') as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM products WHERE barcode = ?", (barcode,))
        product = cur.fetchone()

    if product:
        return {
            "barcode": product[1],
            "product_url": product[2],
            "product_ids": product[3]
        }
    else:
        return {"error": "Product not found"}, 404


@app.route('/product_details', methods=['GET', 'POST'])
def product_details():
    barcode = request.args.get('barcode')
    product_url = request.args.get('url')
    product_ids = request.args.get('ids')
    urun_yorumlari = []

    if request.method == 'GET':
        trendyol_urun = Urun()
        urun_yorumlari = trendyol_urun.yorumlar(product_url, hedef_satici="")
        if not urun_yorumlari:
            urun_yorumlari = []

    return render_template('product_details.html', barcode=barcode, url=product_url, ids=product_ids,
                           urun_yorumlari=urun_yorumlari)


@app.route('/submit_comments', methods=['POST'])
def submit_comments():
    token = session.get('token')
    product_ids = request.form.get('ids')
    yorum_metni = request.form.getlist('yorum_metni')
    yorum_basligi = request.form.getlist('yorum_basligi')
    yorum_puani = request.form.getlist('yorum_puani')
    yorum_index = int(request.form['yorum_index'])

    if not product_ids:
        print("Product IDs boş.")
    else:
        yorum_ekle(token, product_ids, yorum_metni=yorum_metni[yorum_index], yorum_basligi=yorum_basligi[yorum_index],
                   yorum_puani=yorum_puani[yorum_index])

    return redirect(
        url_for('product_details', barcode=request.form.get('barcode'), url=request.form.get('url'), ids=product_ids))


@app.route('/logs')
def view_logs():
    with sqlite3.connect('database.db') as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM logs ORDER BY timestamp DESC")
        logs = cur.fetchall()
    return render_template('logs.html', logs=logs)


@app.route('/logout')
def logout():
    session.pop('token', None)
    return redirect(url_for('index'))


def yorum_ekle(token, product_ids, yorum_metni, yorum_basligi, yorum_puani):
    if not product_ids:
        print("Product IDs boş.")
        return

    if not token:
        print("Token bulunamadı.")
        return

    # Veritabanından rastgele bir CustomerId seç
    with sqlite3.connect('database.db') as con:
        cur = con.cursor()
        cur.execute("SELECT \"Üye Id\" FROM CustomerId ORDER BY RANDOM() LIMIT 1")
        customer_id_row = cur.fetchone()
        if not customer_id_row:
            print("Veritabanında müşteri bulunamadı.")
            return
        customer_id = customer_id_row[0]

    # Product IDs'i virgülle ayrılmış stringden listeye dönüştür
    product_ids_list = product_ids.split(',') if product_ids else []

    for product_id in product_ids_list:
        cookies = {
            'PHPSESSID': '69fa6c045eb793ff3a664bd5c5708cec',
        }

        headers = {
            # 'Cookie': 'PHPSESSID=69fa6c045eb793ff3a664bd5c5708cec',
        }

        files = {
            'image': '',
            'token': (None, token),
            'data': (None, '[{'
                           f'"CustomerId": "{customer_id}",'
                           f'"ProductId": "{product_id}",'
                           f'"Comment": "{yorum_metni}",'
                           f'"Title": "{yorum_basligi}",'
                           f'"Rate": "{yorum_puani}",'
                           f'"IsNameDisplayed": "", '
                           f'"ImageUrl": ""'
                           '}]'
                     ),
        }

        response = requests.post('https://dgnonline.com/rest1/product/comment', cookies=cookies,
                                 headers=headers, files=files)
        if response.status_code != 200:
            print(f"Yorum gönderme başarısız. Status Code: {response.status_code}, {response.text}")
        else:
            print(response.json())


# Urun sınıfı burada olacak
class Urun:
    def __repr__(self) -> str:
        return f"{__class__.__name__} Sınıfı -- Trendyol'dan hedef ürün detaylarını çevirmek için kodlanmıştır."

    def __init__(self):
        """Trendyol'dan hedef ürün detaylarını çevirir"""
        self.__kimlik = {"User-Agent": "pyTrendyol"}

    def yorumlar(self, urun_link: str, hedef_satici: str = None) -> list[dict] or None:
        """Trendyol'dan hedef ürün yorumlarını çevirir, sadece yıldız ve yorumları içerir"""
        if not urun_link:
            print("Ürün linki boş veya geçersiz.")
            return None

        link = self._link_ayristir(urun_link)
        if not link:
            return None

        yorumlar = []

        url = f"https://public-mdc.trendyol.com/discovery-web-socialgw-service/api/review/{link.split('-')[-1]}"
        try:
            istek = get(url, headers=self.__kimlik)
            veriler = istek.json()["result"]["productReviews"]
        except (ConnectionError, KeyError, json.JSONDecodeError) as e:
            print(f"Yorumları alırken bir hata oluştu: {e}")
            return None

        sayfa = 1
        while veriler.get("content"):
            yorumlar.extend(
                {
                    "urun_id": link.split('-')[-1],
                    "yildiz": yorum["rate"],
                    "yorum": yorum["comment"]
                }
                for yorum in veriler["content"] if yorum and (not hedef_satici or yorum["sellerName"] == hedef_satici)
            )

            sayfa += 1
            if sayfa >= veriler["totalPages"]:
                break

            try:
                istek = get(f"{url}?page={sayfa}", headers=self.__kimlik)
                veriler = istek.json()["result"]["productReviews"]
            except (ConnectionError, KeyError, json.JSONDecodeError) as e:
                print(f"Yorumları alırken bir hata oluştu: {e}")
                break

        return yorumlar

    def _link_ayristir(self, urun_link: str) -> str or None:
        """Trendyol'un çeşitli formatlardaki ürün linklerini temizler"""
        if not urun_link:
            return None

        if urun_link.startswith("https://m."):
            url = urun_link.replace("https://m.", "https://")
        elif urun_link.startswith("https://ty.gl"):
            try:
                kisa_link_header = get(urun_link, headers=self.__kimlik, allow_redirects=False).headers.get("location")
                if kisa_link_header:
                    url = self.__ayristir("adjust_redirect=", "&adjust_t=", unquote(kisa_link_header))
                else:
                    url = None
            except KeyError:
                url = None
        else:
            url = urun_link if search(r"http(?:s?):\/\/(?:www\.)?(m?.)?t?", urun_link) else None

        return url.split("?")[0].replace("www.", "") if url else None

    def __ayristir(self, baslangic: str, bitis: str, metin: str) -> str:
        try:
            return metin.split(baslangic)[1].split(bitis)[0]
        except IndexError:
            return None


def fetch_products_page(token, page, size=50, approved='True', on_sale='true'):
    params = {
        'page': page,
        'size': size,
        'approved': approved,
        'onSale': on_sale
    }
    response = requests.get(base_url, headers=headers, params=params)
    if response.status_code != 200:
        return {'error': f"Error: {response.status_code}, {response.text}"}

    data = response.json()
    products = data.get('content', [])
    return {'data': products}


def login(username, password):
    login_data = {
        'user': username,  # Kullanıcı adı
        'pass': password  # Şifre
    }

    login_url = f'https://dgnonline.com/rest1/auth/login/{username}'  # Giriş URL'si
    login_response = requests.post(login_url, data=login_data)  # POST isteği ile giriş yapılır

    if login_response.status_code == 200:  # Eğer giriş başarılıysa
        try:
            token_data = json.loads(login_response.content.decode('utf-8'))  # JSON yanıtı parse et
            token = token_data["data"][0]['token']  # Token değerini al
            return token  # Token'ı döndür
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"Token alınırken bir hata oluştu: {e}")  # Hata mesajı yazdır
            return None
    else:
        print(
            f"Giriş işlemi başarısız. Status Code: {login_response.status_code}")  # Hata durumunda durum kodunu yazdır
        print(f"Response Text: {login_response.text}")  # Hata durumunda yanıt metnini yazdır
        return None


def get_product_ids_by_barcode(token, barcode_code):
    # İlk API çağrısı: Barkod ile ürün bilgisi almak
    barcode_url = f'https://dgnonline.com/rest1/product/getProductByBarcode/{barcode_code}'  # Barkod URL'si
    barcode_params = {
        'token': token,  # Token'ı parametre olarak gönder
    }

    barcode_response = requests.post(barcode_url, data=barcode_params)  # POST isteği ile ürün bilgisi al

    if barcode_response.status_code == 200:  # Eğer ürün bilgisi alma başarılıysa
        try:
            barcode_response_json = barcode_response.json()  # JSON yanıtı parse et
            if barcode_response_json.get("success") and barcode_response_json.get("data"):
                product_ids = []  # Ürün ID'lerini saklamak için liste
                for product in barcode_response_json["data"]:
                    product_code = product.get("ProductCode", "ProductCode bulunamadı")  # Ürün kodunu al

                    # ProductCode'in son kısmını kesmek
                    base_product_code = product_code.rsplit('-', 1)[0]  # Ürün kodunu son kısımdan kes

                    # İkinci API çağrısı: ProductCode ile ProductId almak
                    productcode_url = "https://dgnonline.com/rest1/product/get"  # Ürün kodu URL'si
                    productcode_params = {
                        'token': token,  # Token'ı parametre olarak gönder
                        'f': f'ProductCode|{base_product_code}|contain'  # Filtreleme parametresi
                    }
                    print(base_product_code)

                    productcode_response = requests.post(productcode_url,
                                                         data=productcode_params)  # POST isteği ile ürün ID'si al
                    if productcode_response.status_code == 200:  # Eğer ürün ID'si alma başarılıysa
                        try:
                            productcode_response_json = productcode_response.json()  # JSON yanıtı parse et
                            if productcode_response_json.get("success") and productcode_response_json.get("data"):
                                for product in productcode_response_json["data"]:
                                    product_id = product["ProductId"]  # Ürün ID'sini al
                                    product_ids.append(product_id)  # Ürün ID'sini listeye ekle
                                    print(product_ids)
                            else:
                                print("ProductId yanıtı JSON formatında değil veya boş.")  # Hata mesajı yazdır
                        except json.JSONDecodeError:
                            print("ProductId yanıtı JSON formatında değil.")  # Hata mesajı yazdır
                    else:
                        print(
                            f"ProductId alma işlemi başarısız. Status Code: {productcode_response.status_code}")  # Hata mesajı yazdır
                return product_ids  # Ürün ID'lerini döndür
            else:
                print("Ürün bilgisi yanıtı JSON formatında değil veya boş.")  # Hata mesajı yazdır
                print(f"Yanıt metni: {barcode_response.text}")  # Hata mesajı yazdır
                return None
        except json.JSONDecodeError:
            print("Ürün bilgisi yanıtı JSON formatında değil.")  # Hata mesajı yazdır
            print(f"Yanıt metni: {barcode_response.text}")  # Hata mesajı yazdır
            return None
    else:
        print(f"Ürün bilgisi alma işlemi başarısız. Status Code: {barcode_response.status_code}")  # Hata mesajı yazdır
        print(f"Response Text: {barcode_response.text}")  # Hata mesajı yazdır
        return None


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
