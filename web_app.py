from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import pyodbc
from datetime import datetime
import os
from typing import Iterable
import logging


app = Flask(__name__)
# Configuration from environment with sensible defaults
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'pos_system_ultra_fix_2026')
app.secret_key = app.config['SECRET_KEY']
app.config['DB_SERVER'] = os.environ.get('DB_SERVER', 'localhost\\SQLEXPRESS')
app.config['DB_NAME'] = os.environ.get('DB_NAME', 'PosSystemDB')

# Basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- KẾT NỐI SQL SERVER ---
def get_db():
    # Make database connection configurable via app.config and set autocommit explicitly
    server = app.config.get('DB_SERVER', 'localhost\\SQLEXPRESS')
    database = app.config.get('DB_NAME', 'PosSystemDB')
    conn_str = f'DRIVER={{SQL Server}};SERVER={server};DATABASE={database};Trusted_Connection=yes;'
    # autocommit=True cho các thao tác mặc định, riêng API thanh toán sẽ tắt để kiểm soát Transaction
    conn = pyodbc.connect(conn_str)
    conn.autocommit = True
    return conn


def db_query(sql: str, params: Iterable = ()):  # returns list of rows
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def db_execute(sql: str, params: Iterable = (), commit: bool = False):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        if commit:
            conn.commit()
        return cursor.rowcount
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

@app.before_request
def require_login():
    # Đã thêm 'quen_mat_khau' vào danh sách ngoại lệ
    if 'ten_nv' not in session and request.endpoint not in ['login', 'quen_mat_khau', 'static']:
        return redirect(url_for('login'))


@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    logger.exception('Unhandled exception:')
    return render_template('500.html', message=str(error)), 500

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/xoa-sp/<int:ma_sp>')
def xoa_sp(ma_sp):
    if session.get('quyen') != 'Admin':
        return redirect(url_for('kho_hang'))
    
    try:
        # Dùng đúng bảng Products và cột ID (theo code kho_hang của bạn)
        db_execute("DELETE FROM SanPham WHERE ID = ?", (ma_sp,), commit=True)
        return redirect(url_for('kho_hang'))
        
    except Exception as e:
        logger.exception("Lỗi khi xóa sản phẩm")
        return f"Lỗi khi xóa sản phẩm: {str(e)}", 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login. Basic validation and resource cleanup added."""
    error = None
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''

        if not u or not p:
            error = "Vui lòng nhập tên đăng nhập và mật khẩu."
        else:
            conn = get_db()
            cursor = conn.cursor()
            try:
                cursor.execute('SELECT HoTen, VaiTro FROM NguoiDung WHERE TenDangNhap=? AND MatKhau=?', (u, p))
                user = cursor.fetchone()
                if user:
                    session['ten_nv'] = user[0]
                    session['quyen'] = user[1]
                    session['username'] = u
                    return redirect(url_for('pos'))
                else:
                    error = "Tên đăng nhập hoặc mật khẩu không đúng."
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass

    return render_template('login.html', error=error)

# --- TÍNH NĂNG MỚI: QUÊN MẬT KHẨU ---
@app.route('/quen-mat-khau', methods=['GET', 'POST'])
def quen_mat_khau():
    thong_bao = None
    loai_thong_bao = "error" 

    if request.method == 'POST':
        u = request.form.get('username')
        f = request.form.get('fullname')
        new_p = request.form.get('new_password')
        
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM NguoiDung WHERE TenDangNhap=? AND HoTen=?', (u, f))
        user = cursor.fetchone()
        
        if user:
            cursor.execute('UPDATE NguoiDung SET MatKhau=? WHERE TenDangNhap=?', (new_p, u))
            thong_bao = "Cập nhật mật khẩu thành công! Vui lòng đăng nhập lại."
            loai_thong_bao = "success"
        else:
            thong_bao = "Tên đăng nhập hoặc Họ và tên không chính xác!"
            
    return render_template('quen_mat_khau.html', thong_bao=thong_bao, loai_thong_bao=loai_thong_bao)

@app.route('/pos')
def pos():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT ID, TenSanPham, Gia, SoLuong FROM SanPham')
    sp = cursor.fetchall()
    return render_template('pos.html', san_pham=sp, ten_nv=session['ten_nv'], quyen=session['quyen'])

@app.route('/api/thanh-toan', methods=['POST'])
def thanh_toan():
    payload = request.get_json(silent=True) or {}
    data = payload.get('cart', [])
    paid = float(payload.get('paid') or 0)
    method = payload.get('method', 'cash')

    conn = get_db()
    # Tắt autocommit để bắt đầu Transaction (bảo vệ toàn vẹn dữ liệu)
    conn.autocommit = False 
    cursor = conn.cursor()

    try:
        now = datetime.now()
        ma_hd = f"HD{now.strftime('%y%m%d%H%M%S')}"
        created_str = now.strftime("%Y-%m-%d %H:%M:%S")
        tong = sum((item.get('price', 0) or 0) * (item.get('quantity', 0) or 0) for item in data)

        # 1. Tạo hóa đơn (lưu cả ngày và thời gian)
        # include customer name if provided
        customer = payload.get('customer') if isinstance(payload, dict) else None
        if customer:
            try:
                cursor.execute('INSERT INTO HoaDon (MaHoaDon, NguoiBan, NgayTao, TongTien, KhachHang) VALUES (?,?,?,?,?)', 
                               (ma_hd, session.get('ten_nv'), created_str, tong, customer))
            except Exception:
                # fallback to original schema without Customer column
                cursor.execute('INSERT INTO HoaDon (MaHoaDon, NguoiBan, NgayTao, TongTien) VALUES (?,?,?,?)', 
                               (ma_hd, session.get('ten_nv'), created_str, tong))
        else:
            cursor.execute('INSERT INTO HoaDon (MaHoaDon, NguoiBan, NgayTao, TongTien) VALUES (?,?,?,?)', 
                           (ma_hd, session.get('ten_nv'), created_str, tong))

        # 2. Trừ tồn kho và thêm chi tiết hóa đơn
        for item in data:
            # use parameterized updates
            qty = item.get('quantity', 0) or 0
            name = item.get('name')
            price = item.get('price', 0) or 0
            cursor.execute('UPDATE SanPham SET SoLuong = SoLuong - ? WHERE TenSanPham = ?', (qty, name))
            cursor.execute('INSERT INTO ChiTietHoaDon (MaHoaDon, TenSanPham, SoLuong, DonGia) VALUES (?,?,?,?)', 
                           (ma_hd, name, qty, price))

        # Lưu tất cả thay đổi vào DB nếu không có lỗi
        conn.commit()
        change = round(paid - tong, 2) if paid else 0
        if change < 0:
            change = 0
        return jsonify({"status": "success", "invoice": ma_hd, "created": created_str, "total": tong, "paid": paid, "method": method, "change": change})

    except Exception as e:
        # Nếu có bất kỳ lỗi gì (VD: sập DB giữa chừng), hoàn tác tất cả các thay đổi
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"status": "error", "message": "Lỗi hệ thống khi thanh toán"}), 500
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

@app.route('/kho-hang', methods=['GET', 'POST'])
def kho_hang():
    # Use helper functions to ensure connections are closed correctly
    if request.method == 'POST' and session.get('quyen') == 'Admin':
        ma = request.form.get('ma_sp')
        ten = request.form.get('ten_sp')
        gia = request.form.get('gia_ban')
        ton = request.form.get('so_luong')
        hinh = request.form.get('hinh') or None

        if ma:
            # Try to update ImageURL column if it exists; fall back to previous schema
            try:
                db_execute('UPDATE SanPham SET TenSanPham=?, Gia=?, SoLuong=?, ImageURL=? WHERE ID=?', (ten, gia, ton, hinh, ma), commit=True)
            except Exception:
                db_execute('UPDATE SanPham SET TenSanPham=?, Gia=?, SoLuong=? WHERE ID=?', (ten, gia, ton, ma), commit=True)
        else:
            try:
                db_execute('INSERT INTO SanPham (TenSanPham, Gia, SoLuong, ImageURL) VALUES (?,?,?,?)', (ten, gia, ton, hinh), commit=True)
            except Exception:
                db_execute('INSERT INTO SanPham (TenSanPham, Gia, SoLuong) VALUES (?,?,?)', (ten, gia, ton), commit=True)

        return redirect(url_for('kho_hang'))

    # Try to read ImageURL if present in DB; fall back to older schema without ImageURL
    try:
        sp = db_query('SELECT ID, TenSanPham, Gia, SoLuong, ImageURL FROM SanPham')
    except Exception:
        sp = db_query('SELECT ID, TenSanPham, Gia, SoLuong FROM SanPham')

    return render_template('kho_hang.html', san_pham=sp, ten_nv=session['ten_nv'], quyen=session['quyen'])

@app.route('/nhan-vien', methods=['GET', 'POST'])
def nhan_vien():
    conn = get_db()
    cursor = conn.cursor()
    if request.method == 'POST' and session.get('quyen') == 'Admin':
        u = request.form.get('u')
        p = request.form.get('p')
        t = request.form.get('t')
        q = request.form.get('q')
        edit_u = request.form.get('user_edit')
        
        if edit_u:
            cursor.execute('UPDATE NguoiDung SET MatKhau=?, HoTen=?, VaiTro=? WHERE TenDangNhap=?', (p, t, q, edit_u))
        else:
            cursor.execute('INSERT INTO NguoiDung VALUES (?,?,?,?)', (u, p, t, q))
        return redirect(url_for('nhan_vien'))
        
    cursor.execute('SELECT TenDangNhap, MatKhau, HoTen, VaiTro FROM NguoiDung')
    list_nv = cursor.fetchall()
    return render_template('nhan_vien.html', nhan_vien=list_nv, ten_nv=session['ten_nv'], quyen=session['quyen'])

@app.route('/xoa-nv/<user>')
def xoa_nv(user):
    if session.get('quyen') == 'Admin' and user != 'admin':
        get_db().cursor().execute('DELETE FROM NguoiDung WHERE TenDangNhap=?', (user,))
    return redirect(url_for('nhan_vien'))

@app.route('/lich-su')
def lich_su():
    conn = get_db()
    cursor = conn.cursor()
    # Try to include Customer column if available
    try:
        cursor.execute('SELECT MaHoaDon, NguoiBan, NgayTao, TongTien, KhachHang FROM HoaDon ORDER BY MaHoaDon DESC')
        hd = cursor.fetchall()
    except Exception:
        cursor.execute('SELECT MaHoaDon, NguoiBan, NgayTao, TongTien FROM HoaDon ORDER BY MaHoaDon DESC')
        hd = cursor.fetchall()
    return render_template('lich_su.html', hoa_don=hd, ten_nv=session['ten_nv'], quyen=session['quyen'])

@app.route('/api/chi-tiet/<ma_hd>')
def chi_tiet(ma_hd):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT TenSanPham, SoLuong, DonGia FROM ChiTietHoaDon WHERE MaHoaDon=?', (ma_hd,))
    rows = cursor.fetchall()
    return jsonify([{"name": r[0], "qty": r[1], "price": r[2]} for r in rows])

@app.route('/thong-ke')
def thong_ke():
    # Render UI — data will be requested via AJAX to allow filtering and export
    return render_template('thong_ke.html', ten_nv=session['ten_nv'], quyen=session['quyen'])


@app.route('/api/thong-ke')
def api_thong_ke():
    # Returns JSON with labels and values for chart and a rows list for table
    start = request.args.get('start')
    end = request.args.get('end') 
    conn = get_db()
    cursor = conn.cursor()
    # Build base SQL. CreatedDate stored as string/datetime; use CONVERT(date, CreatedDate) for grouping
    sql = 'SELECT CONVERT(date, NgayTao) AS d, SUM(TongTien) FROM HoaDon '
    params = []
    if start and end:
        sql += 'WHERE CONVERT(date, NgayTao) BETWEEN ? AND ? '
        params = [start, end]
    sql += 'GROUP BY CONVERT(date, NgayTao) ORDER BY CONVERT(date, NgayTao)'
    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        labels = [r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0]) for r in rows]
        values = [float(r[1] or 0) for r in rows]
        table = [{'date': labels[i], 'total': values[i]} for i in range(len(labels))]
        return jsonify({'labels': labels, 'values': values, 'rows': table})
    except Exception as e:
        logger.exception('Error querying thong-ke')
        return jsonify({'labels': [], 'values': [], 'rows': []}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)