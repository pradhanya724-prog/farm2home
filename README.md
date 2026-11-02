# Farm2Home — Smart Farm-to-Home Marketplace

A Flask application based on the supplied project report, with differentiating features:
- Rule-based freshness score using harvest date, storage condition and transport time.
- Batch passport with unique batch ID and downloadable QR code.
- Price context (farmer listing price and optional reference price).
- Surplus harvest listings.
- Farmer harvest calendar and simple sales dashboard.
- Buyer ordering and farmer order-status updates.
- Optional Amazon S3 image uploads and Amazon SNS notifications.
- MySQL-compatible configuration for Amazon RDS; SQLite is used locally for easy setup.

## 1. Run locally in VS Code (Windows)

1. Install Python 3.11 or newer and VS Code.
2. Extract this folder and open it in VS Code.
3. Open Terminal > New Terminal and run:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python app.py
```

4. Open http://127.0.0.1:5000

Demo administrator:
- Email: `admin@farm2home.demo`
- Password: `Farm2Home@123`

Change this password and SECRET_KEY before any deployment. The first database is created automatically.

## 2. Local testing accounts

Register a Farmer account and a Buyer account using the website. Add produce as the farmer, then log in as the buyer and place an order. The farmer can update order status from the Orders page.

## 3. Configure Amazon RDS MySQL

Create a MySQL database in Amazon RDS. For a real deployment, place RDS in private subnets and allow inbound TCP 3306 only from the EC2 application security group.

Set in `.env`:

```env
DATABASE_URL=mysql+pymysql://DB_USER:URL_ENCODED_PASSWORD@RDS_ENDPOINT:3306/farm2home
```

URL-encode special characters in the database password. Install the listed dependencies and restart the application. For a new database, `db.create_all()` creates the tables. For production upgrades, use database migrations rather than relying on `create_all()`.

## 4. Configure Amazon S3 and SNS

Create a private S3 bucket in `ap-south-1`; keep Block Public Access enabled and enable default encryption. Create an SNS topic and confirm an email subscription.

Set:

```env
AWS_REGION=ap-south-1
S3_BUCKET=your-private-bucket-name
SNS_TOPIC_ARN=arn:aws:sns:ap-south-1:YOUR_ACCOUNT_ID:your-topic
ENABLE_AWS=true
```

Attach an EC2 instance role with only the required permissions:
- `s3:PutObject`, `s3:GetObject` for the product object prefix.
- `sns:Publish` for the configured SNS topic.

Do not place AWS access keys in source code or commit `.env`. S3 URLs in this prototype are pre-signed and expire after one hour. A production app should generate a fresh pre-signed URL when rendering each page.

## 5. Deploy to EC2

For a learning deployment, use Ubuntu or Amazon Linux EC2, install Python, copy the project, create a virtual environment, install requirements, configure environment variables and run:

```bash
gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
```

For production, put Nginx and an Application Load Balancer in front of Gunicorn, configure HTTPS, use a process manager (systemd), store secrets in AWS Secrets Manager or Parameter Store, and use a managed database. Configure the target group health check to `/`.

## 6. Important scope and safety notes

- Freshness is a transparent demonstration score, not a laboratory test or food-safety certification. It must not be used to guarantee that food is safe.
- The reference price is entered by the farmer; this starter project does not fetch live mandi prices.
- The QR page displays the batch information recorded by the farmer; it is not independent supply-chain verification.
- Payment gateway integration, SMS delivery, document-based farmer verification, production-grade audit logs, CSRF protection, rate limiting and automated migrations are not implemented in this academic prototype.
- For public deployment, add CSRF protection, email verification, password reset, secure cookie settings, HTTPS-only cookies, rate limiting, migrations, robust role-based admin controls, and a proper production secret.
- The initial demo admin is created only when the database is empty. Change its password immediately and do not expose demo credentials in a public deployment.

## 7. AWS architecture mapping

| Report service | How this project uses it |
|---|---|
| Amazon VPC | Network isolation configured in AWS |
| EC2 | Runs Flask application |
| Application Load Balancer | Distributes traffic to EC2 targets |
| EC2 Auto Scaling | Scales EC2 application instances |
| Amazon RDS | Stores users, products, harvests and orders |
| Amazon S3 | Stores product images privately |
| Amazon SNS | Publishes order notifications |
| CloudWatch | Collects EC2/ALB/RDS metrics and logs when configured |
| Route 53 | Maps domain to the load balancer when configured |

CloudWatch, Route 53, VPC, ALB and Auto Scaling are AWS infrastructure configuration tasks; the Flask source code does not create those resources automatically.
