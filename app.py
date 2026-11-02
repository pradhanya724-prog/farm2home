import os, io, uuid, secrets
from datetime import datetime, date
from functools import wraps
from urllib.parse import quote

import boto3
import qrcode
from dotenv import load_dotenv
from flask import Flask, render_template, redirect, url_for, flash, request, abort, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

load_dotenv()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-this-secret")
db_url = os.getenv("DATABASE_URL", "").strip()
app.config["SQLALCHEMY_DATABASE_URI"] ="sqlite:///farm2home.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

AWS_ENABLED = os.getenv("ENABLE_AWS", "false").lower() == "true"
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
S3_BUCKET = os.getenv("S3_BUCKET", "")
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "")
ALLOWED = {"png", "jpg", "jpeg", "webp"}

def utcnow():
    return datetime.utcnow()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="buyer")
    phone = db.Column(db.String(30))
    village = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=utcnow)
    products = db.relationship("Product", backref="farmer", lazy=True)
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Float, nullable=False)
    reference_price = db.Column(db.Float, nullable=False, default=0)
    quantity = db.Column(db.Float, nullable=False, default=0)
    unit = db.Column(db.String(20), default="kg")
    harvest_date = db.Column(db.Date, nullable=False)
    storage = db.Column(db.String(40), default="Normal")
    transport_hours = db.Column(db.Float, default=0)
    image_key = db.Column(db.String(300))
    image_url = db.Column(db.String(700))
    batch_code = db.Column(db.String(40), unique=True, nullable=False)
    is_surplus = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    @property
    def freshness(self):
        age = max(0, (date.today() - self.harvest_date).days)
        storage_bonus = {"Refrigerated": 8, "Cool shaded": 4, "Normal": 0}.get(self.storage, 0)
        transport_penalty = min(20, max(0, int(self.transport_hours * 1.2)))
        score = max(10, min(100, 100 - age * 9 + storage_bonus - transport_penalty))
        return int(score)
    @property
    def freshness_label(self):
        return "Very fresh" if self.freshness >= 85 else ("Fresh" if self.freshness >= 65 else ("Use soon" if self.freshness >= 40 else "Limited freshness"))
    @property
    def buyer_price(self):
        return round(self.price * 1.08, 2) if self.is_surplus is False else round(self.price * 0.95, 2)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default="Placed")
    created_at = db.Column(db.DateTime, default=utcnow)
    buyer = db.relationship("User", foreign_keys=[buyer_id])
    product = db.relationship("Product")

class Harvest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    crop_name = db.Column(db.String(100), nullable=False)
    expected_date = db.Column(db.Date, nullable=False)
    expected_quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default="kg")
    notes = db.Column(db.String(300))
    farmer = db.relationship("User")

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator

def publish_sns(subject, message):
    if AWS_ENABLED and SNS_TOPIC_ARN:
        boto3.client("sns", region_name=AWS_REGION).publish(
            TopicArn=SNS_TOPIC_ARN, Subject=subject[:100], Message=message
        )

def save_image(file):
    if not file or not file.filename:
        return None, None
    filename = secure_filename(file.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise ValueError("Upload JPG, JPEG, PNG or WEBP images only.")
    key = f"products/{uuid.uuid4().hex}.{ext}"
    if AWS_ENABLED and S3_BUCKET:
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.upload_fileobj(file, S3_BUCKET, key, ExtraArgs={"ContentType": file.mimetype or "image/jpeg"})
        url = s3.generate_presigned_url("get_object", Params={"Bucket": S3_BUCKET, "Key": key}, ExpiresIn=3600)
        return key, url
    # Local demo fallback: use a static local path.
    local_name = f"{uuid.uuid4().hex}.{ext}"
    target = os.path.join(app.root_path, "static", "uploads")
    os.makedirs(target, exist_ok=True)
    file.save(os.path.join(target, local_name))
    return key, url_for("static", filename=f"uploads/{local_name}")

@app.route("/")
def home():
    featured = Product.query.filter(Product.quantity > 0).order_by(Product.created_at.desc()).limit(8).all()
    return render_template("home.html", featured=featured)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "buyer")
        if role not in {"farmer", "buyer"}:
            flash("Choose a valid account type.", "danger")
        elif not name or not email or len(password) < 8:
            flash("Enter your name, email and a password of at least 8 characters.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "warning")
        else:
            user = User(name=name, email=email, role=role, phone=request.form.get("phone"), village=request.form.get("village"))
            user.set_password(password)
            db.session.add(user); db.session.commit()
            login_user(user)
            flash("Welcome to Farm2Home!", "success")
            return redirect(url_for("dashboard"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "farmer":
        products = Product.query.filter_by(farmer_id=current_user.id).all()
        orders = Order.query.join(Product).filter(Product.farmer_id == current_user.id).order_by(Order.created_at.desc()).limit(8).all()
        harvests = Harvest.query.filter_by(farmer_id=current_user.id).order_by(Harvest.expected_date.asc()).all()
        revenue = sum(o.quantity * o.unit_price for o in Order.query.join(Product).filter(Product.farmer_id == current_user.id, Order.status != "Cancelled").all())
        return render_template("farmer_dashboard.html", products=products, orders=orders, harvests=harvests, revenue=revenue)
    if current_user.role == "admin":
        return render_template("admin_dashboard.html", users=User.query.count(), products=Product.query.count(), orders=Order.query.count(), recent=Order.query.order_by(Order.created_at.desc()).limit(10).all())
    orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.created_at.desc()).limit(8).all()
    return render_template("buyer_dashboard.html", orders=orders)

@app.route("/products")
def products():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    query = Product.query.filter(Product.quantity > 0)
    if q:
        query = query.filter(Product.name.ilike(f"%{q}%"))
    if category:
        query = query.filter_by(category=category)
    items = query.order_by(Product.created_at.desc()).all()
    categories = [x[0] for x in db.session.query(Product.category).distinct().all()]
    return render_template("products.html", products=items, categories=categories, q=q, selected_category=category)

@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = db.session.get(Product, product_id) or abort(404)
    return render_template("product_detail.html", product=product)

@app.route("/product/add", methods=["GET", "POST"])
@role_required("farmer")
def add_product():
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            price = float(request.form.get("price", 0))
            qty = float(request.form.get("quantity", 0))
            reference = float(request.form.get("reference_price", 0) or 0)
            harvest_date = datetime.strptime(request.form.get("harvest_date"), "%Y-%m-%d").date()
            if not name or price <= 0 or qty <= 0:
                raise ValueError("Enter a product name, positive price and positive quantity.")
            key, image_url = save_image(request.files.get("image"))
            product = Product(
                farmer_id=current_user.id, name=name, category=request.form.get("category", "Other"),
                description=request.form.get("description", ""), price=price, reference_price=reference,
                quantity=qty, unit=request.form.get("unit", "kg"), harvest_date=harvest_date,
                storage=request.form.get("storage", "Normal"),
                transport_hours=float(request.form.get("transport_hours", 0) or 0),
                image_key=key, image_url=image_url, batch_code="F2H-" + secrets.token_hex(4).upper(),
                is_surplus=bool(request.form.get("is_surplus"))
            )
            db.session.add(product); db.session.commit()
            flash("Your produce is now listed!", "success")
            return redirect(url_for("product_detail", product_id=product.id))
        except (ValueError, TypeError) as e:
            flash(str(e) or "Please check the form values.", "danger")
    return render_template("add_product.html")

@app.route("/harvest/add", methods=["POST"])
@role_required("farmer")
def add_harvest():
    try:
        item = Harvest(farmer_id=current_user.id, crop_name=request.form["crop_name"],
                       expected_date=datetime.strptime(request.form["expected_date"], "%Y-%m-%d").date(),
                       expected_quantity=float(request.form["expected_quantity"]),
                       unit=request.form.get("unit", "kg"), notes=request.form.get("notes", ""))
        db.session.add(item); db.session.commit()
        flash("Harvest added to your crop calendar.", "success")
    except Exception:
        db.session.rollback(); flash("Please enter valid harvest details.", "danger")
    return redirect(url_for("dashboard"))

@app.route("/order/<int:product_id>", methods=["POST"])
@role_required("buyer")
def place_order(product_id):
    product = db.session.get(Product, product_id) or abort(404)
    try:
        qty = float(request.form.get("quantity", 0))
        if qty <= 0 or qty > product.quantity:
            raise ValueError
        order = Order(buyer_id=current_user.id, product_id=product.id, quantity=qty, unit_price=product.buyer_price)
        product.quantity -= qty
        db.session.add(order); db.session.commit()
        publish_sns("New Farm2Home order", f"Order #{order.id} for {qty} {product.unit} of {product.name}.")
        flash(f"Order #{order.id} placed successfully.", "success")
    except (ValueError, TypeError):
        db.session.rollback(); flash("Enter a valid quantity within available stock.", "danger")
    return redirect(url_for("dashboard"))

@app.route("/orders")
@login_required
def orders():
    if current_user.role == "farmer":
        rows = Order.query.join(Product).filter(Product.farmer_id == current_user.id).order_by(Order.created_at.desc()).all()
    elif current_user.role == "admin":
        rows = Order.query.order_by(Order.created_at.desc()).all()
    else:
        rows = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("orders.html", orders=rows)

@app.route("/order/<int:order_id>/status", methods=["POST"])
@role_required("farmer", "admin")
def update_order(order_id):
    order = db.session.get(Order, order_id) or abort(404)
    if current_user.role == "farmer" and order.product.farmer_id != current_user.id:
        abort(403)
    status = request.form.get("status")
    if status not in {"Accepted", "Packed", "Dispatched", "Delivered", "Cancelled"}:
        abort(400)
    if status == "Cancelled" and order.status != "Cancelled":
        order.product.quantity += order.quantity
    order.status = status
    db.session.commit()
    publish_sns("Farm2Home order update", f"Order #{order.id} status changed to {status}.")
    flash("Order status updated.", "success")
    return redirect(url_for("orders"))

@app.route("/trace/<batch_code>")
def trace(batch_code):
    product = Product.query.filter_by(batch_code=batch_code).first_or_404()
    return render_template("trace.html", product=product)

@app.route("/qr/<int:product_id>")
def qr_code(product_id):
    product = db.session.get(Product, product_id) or abort(404)
    url = url_for("trace", batch_code=product.batch_code, _external=True)
    img = qrcode.make(url)
    output = io.BytesIO(); img.save(output, format="PNG"); output.seek(0)
    return send_file(output, mimetype="image/png", download_name=f"{product.batch_code}.png")

@app.route("/admin/verify/<int:user_id>", methods=["POST"])
@role_required("admin")
def verify_farmer(user_id):
    # Prototype verification flag is stored in the user's village field as a simple status marker.
    farmer = db.session.get(User, user_id) or abort(404)
    if farmer.role != "farmer": abort(400)
    farmer.village = (farmer.village or "") .replace(" [VERIFIED]", "") + " [VERIFIED]"
    db.session.commit()
    flash(f"{farmer.name} marked as verified.", "success")
    return redirect(url_for("dashboard"))

@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, message="You do not have permission to view this page."), 403

@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="The page or item could not be found."), 404

with app.app_context():
    db.create_all()
    # Seed a demo administrator only when the database has no users.
    if User.query.count() == 0:
        admin = User(name="Farm2Home Admin", email="admin@farm2home.demo", role="admin")
        admin.set_password("Farm2Home@123")
        db.session.add(admin); db.session.commit()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=os.getenv("FLASK_DEBUG", "true").lower() == "true")
