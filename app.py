"""
Bu Flask uygulaması, Trendyol API'si ile etkileşime girerek ürün bilgilerini ve yorumlarını çeker.
Kullanıcılar giriş yaparak ürünleri arayabilir, yorum ekleyebilir ve ürün detaylarını görüntüleyebilir.
Ayrıca müşteri kimlik bilgilerini bir Excel dosyasından yükleyebilirler.
"""

import json
import sqlite3
import pandas as pd
import requests
from flask import Flask, render_template, request, redirect, url_for, session
from requests import get
from requests.exceptions import ConnectionError
from urllib.parse import unquote
from re import search
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit

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
        cur.execute('''CREATE TABLE IF NOT EXISTS products_with_comments
                       (id INTEGER PRIMARY KEY, barcode TEXT UNIQUE, product_url TEXT, product_ids TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS products_without_comments
                       (id INTEGER PRIMARY KEY, barcode TEXT UNIQUE, product_url TEXT, product_ids TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS CustomerId
                       (id INTEGER PRIMARY KEY, customer_id TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS comments
                       (id INTEGER PRIMARY KEY, urun_id TEXT, yildiz INTEGER, yorum TEXT UNIQUE)''')
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
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    if request.method == 'POST':
        if 'file' in request.files:
            file = request.files['file']
            if file.filename != '':
                file_path = 'customer_ids.xlsx'
                file.save(file_path)
                load_customer_ids_from_excel(file_path)
                return redirect(url_for('products'))

        page_num = 1
        while True:
            response = fetch_products_page(token, page_num)  # Doğru parametrelerle çağırıyoruz
            if response.get('error'):
                return response['error']
            data = response['data']
            if not data:
                break

            process_products(token, data)  # Modüler hale getirildi

            page_num += 1

    with sqlite3.connect('database.db') as con:
        cur = con.cursor()
        products_with_comments, total_products_with_comments = fetch_products_from_db(cur, 'products_with_comments',
                                                                                      query, per_page, offset)
        products_without_comments, total_products_without_comments = fetch_products_from_db(cur,
                                                                                            'products_without_comments',
                                                                                            query, per_page, offset)

    total_products_with_comments_pages = (total_products_with_comments + per_page - 1) // per_page
    total_products_without_comments_pages = (total_products_without_comments + per_page - 1) // per_page
    prev_page = page - 1 if page > 1 else None
    next_page = page + 1 if page < total_products_with_comments_pages else None

    return render_template('products.html',
                           products_with_comments=products_with_comments,
                           products_without_comments=products_without_comments,
                           query=query,
                           prev_page=prev_page,
                           next_page=next_page,
                           total_pages=total_products_with_comments_pages,
                           total_without_comments_pages=total_products_without_comments_pages,
                           page=page)


@app.route('/stop_fetching', methods=['POST'])
def stop_fetching():
    session['stop_fetching'] = True
    return redirect(url_for('products'))


def fetch_products_page(token, page, size=50, approved='True', on_sale='true', retries=3):
    params = {
        'page': page,
        'size': size,
        'approved': approved,
        'onSale': on_sale
    }

    for attempt in range(retries):
        try:
            response = requests.get(base_url, headers=headers, params=params)
            response.raise_for_status()  # Bu satır, 4XX ve 5XX hataları için istisna oluşturur.
            data = response.json()
            products = data.get('content', [])
            return {'data': products}
        except requests.exceptions.RequestException as e:
            print(f"Attempt {attempt + 1} failed for page {page}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Geri çekilme (exponential backoff) ile bekleme
            else:
                return {'error': f"Error: {response.status_code}, {response.text}"}
    return {'error': f"Failed to fetch page {page} after {retries} attempts"}


def fetch_all_products(token):
    page_num = 1
    total_pages = None

    while True:
        response = fetch_products_page(token, page_num)
        if response.get('error'):
            print(response['error'])
            break  # Hata durumunda döngüden çık
        data = response['data']
        if not data:
            print(f"No more products to fetch on page {page_num}. Restarting from page 1.")
            page_num = 1
            continue  # Sayfa numarasını sıfırla ve baştan başla

        # Verileri işle
        process_products(data)

        # Sayfa numarasını artır
        page_num += 1


def process_products(token, products):
    with sqlite3.connect('database.db') as con:
        cur = con.cursor()

        for product in products:
            barcode = product['barcode']
            product_url = product['productUrl']
            product_ids = get_product_ids_by_barcode(token, barcode)

            # Urun yorumlarını kontrol et
            trendyol_urun = Urun()
            urun_yorumlari = trendyol_urun.yorumlar(product_url)
            table_name = 'products_with_comments' if urun_yorumlari else 'products_without_comments'

            # product_ids'in boş olmadığını kontrol et
            if product_ids:
                product_info = {
                    'barcode': barcode,
                    'product_url': product_url,
                    'product_ids': ','.join(product_ids) if product_ids else None
                }

                # Veritabanında ürün zaten var mı diye kontrol et
                cur.execute(f"SELECT * FROM {table_name} WHERE barcode = ?", (barcode,))
                existing_product = cur.fetchone()

                if existing_product:
                    # Eğer ürün varsa ve bilgileri değişmişse güncelle
                    if (existing_product[2] != product_info['product_url'] or
                            existing_product[3] != product_info['product_ids']):
                        cur.execute(
                            f"UPDATE {table_name} SET product_url = ?, product_ids = ? WHERE barcode = ?",
                            (product_info['product_url'], product_info['product_ids'], barcode))
                        con.commit()
                else:
                    # Eğer ürün yoksa yeni kayıt olarak ekle
                    cur.execute(
                        f"INSERT INTO {table_name} (barcode, product_url, product_ids) VALUES (?, ?, ?)",
                        (product_info['barcode'], product_info['product_url'], product_info['product_ids']))
                    con.commit()


def fetch_products_from_db(cur, table_name, query, per_page, offset):
    if query:
        cur.execute(f"SELECT * FROM {table_name} WHERE barcode LIKE ? LIMIT ? OFFSET ?",
                    ('%' + query + '%', per_page, offset))
    else:
        cur.execute(f"SELECT * FROM {table_name} LIMIT ? OFFSET ?", (per_page, offset))
    products = cur.fetchall()

    if query:
        cur.execute(f"SELECT COUNT(*) FROM {table_name} WHERE barcode LIKE ?", ('%' + query + '%',))
    else:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
    total_products = cur.fetchone()[0]

    return products, total_products


@app.route('/products/<barcode>')
def product_detail(barcode):
    with sqlite3.connect('database.db') as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM products_with_comments WHERE barcode = ?", (barcode,))
        product = cur.fetchone()

        if not product:
            cur.execute("SELECT * FROM products_without_comments WHERE barcode = ?", (barcode,))
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
        urun_yorumlari = trendyol_urun.yorumlar(product_url)
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

    def yorumlar(self, urun_link: str, hedef_satici=None) -> list[dict] or None:
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
            veriler = istek.json().get("result", {}).get("productReviews", None)
        except (ConnectionError, KeyError, json.JSONDecodeError) as e:
            print(f"Yorumları alırken bir hata oluştu: {e}")
            return None

        if not veriler:
            print("Veriler alınamadı veya boş döndü.")
            return None

        sayfa = 1
        while veriler and veriler.get("content"):
            for yorum in veriler["content"]:
                if yorum["rate"] >= 3:  # Yorumun en az 3 yıldızlı olup olmadığını kontrol edin
                    yorumlar.append({
                        "urun_id": link.split('-')[-1],
                        "yildiz": yorum["rate"],
                        "yorum": yorum["comment"]
                    })

            sayfa += 1
            if sayfa >= veriler["totalPages"]:
                break

            try:
                istek = get(f"{url}?page={sayfa}", headers=self.__kimlik)
                veriler = istek.json().get("result", {}).get("productReviews", None)
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


def fetch_products_page(token, page, size=50, approved='True', on_sale='true', retries=3):
    params = {
        'page': page,
        'size': size,
        'approved': approved,
        'onSale': on_sale
    }

    for attempt in range(retries):
        try:
            response = requests.get(base_url, headers=headers, params=params)
            response.raise_for_status()  # Bu satır, 4XX ve 5XX hataları için istisna oluşturur.
            data = response.json()
            products = data.get('content', [])
            return {'data': products}
        except requests.exceptions.RequestException as e:
            print(f"Attempt {attempt + 1} failed for page {page}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Geri çekilme (exponential backoff) ile bekleme
            else:
                return {'error': f"Error: {response.status_code}, {response.text}"}
    return {'error': f"Failed to fetch page {page} after {retries} attempts"}


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
                    print(base_product_code)

                    # İkinci API çağrısı: ProductCode ile ProductId almak
                    productcode_url = "https://dgnonline.com/rest1/product/get"  # Ürün kodu URL'si
                    productcode_params = {
                        'token': token,  # Token'ı parametre olarak gönder
                        'f': f'ProductCode|{base_product_code}|contain'  # Filtreleme parametresi
                    }
                    print(base_product_code)

                    productcode_response = requests.post(productcode_url,
                                                         data=productcode_params)  # POST isteği ile ürün ID'si al
                    print(productcode_response)

                    if productcode_response.status_code == 200:  # Eğer ürün ID'si alma başarılıysa
                        try:
                            productcode_response_json = productcode_response.json()  # JSON yanıtı parse et
                            if productcode_response_json.get("success") and productcode_response_json.get("data"):
                                for product in productcode_response_json["data"]:
                                    product_id = product["ProductId"]  # Ürün ID'sini al
                                    product_ids.append(product_id)  # Ürün ID'sini listeye ekle
                                    print(product_id)
                                    print(product_ids)
                                    print(productcode_response_json)
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


def scheduled_task():
    print("Scheduled task running...")
    token = "your_token"  # Otomasyon için uygun bir token alın
    fetch_all_products(token)
    print("Scheduled task completed.")


if __name__ == '__main__':
    init_db()

    # Flask uygulamasını çalıştır
    app.run(debug=True, use_reloader=False)  # use_reloader=False ile yeniden başlatmayı önler

    # Zamanlayıcıyı başlat
    scheduler = BackgroundScheduler()
    scheduler.start()

    # Her saat başında scheduled_task fonksiyonunu çalıştır
    scheduler.add_job(
        func=scheduled_task,
        trigger=IntervalTrigger(hours=1),
        id='fetch_products_job',
        name='Fetch products every hour',
        replace_existing=True)

    # Uygulama sonlandığında zamanlayıcıyı durdur
    atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
