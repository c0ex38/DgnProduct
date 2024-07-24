from flask import Flask, render_template, request, redirect, url_for, session
import requests
import json

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

def fetch_and_print_products(token, size=50, approved='True', on_sale='true'):
    products_info = []
    page = 0

    while True:
        params = {
            'page': page,
            'size': size,
            'approved': approved,
            'onSale': on_sale
        }
        response = requests.get(base_url, headers=headers, params=params)
        if response.status_code != 200:
            return f"Error: {response.status_code}, {response.text}"

        data = response.json()
        products = data.get('content', [])
        if not products:
            break

        for product in products:
            barcode = product.get('barcode')
            product_url = product.get('productUrl')
            product_ids = get_product_ids_by_barcode(token, barcode)
            if product_ids is not None:
                products_info.append({
                    'barcode': barcode,
                    'product_url': product_url,
                    'product_ids': product_ids
                })
                print(products_info)

        page += 1

    return products_info

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
        print(f"Giriş işlemi başarısız. Status Code: {login_response.status_code}")  # Hata durumunda durum kodunu yazdır
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
                        print(f"ProductId alma işlemi başarısız. Status Code: {productcode_response.status_code}")  # Hata mesajı yazdır
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

    products_info = None
    if request.method == 'POST':
        products_info = fetch_and_print_products(token)

    return render_template('products.html', products=products_info)

if __name__ == '__main__':
    app.run(debug=True)
