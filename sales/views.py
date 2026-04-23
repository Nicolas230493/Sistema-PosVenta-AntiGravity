from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, FileResponse
from products.models import Product, InventoryMovement
from customers.models import Customer
from .models import Sale, SaleDetail
from .utils import generate_sale_pdf, generate_thermal_ticket, generate_total_sales_report
import json

@login_required
def export_sale_pdf(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=Venta_{sale.id}.pdf'
    generate_sale_pdf(response, sale)
    return response

@login_required
def export_thermal_ticket(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    buffer = generate_thermal_ticket(sale)
    return FileResponse(buffer, as_attachment=False, filename=f"Ticket_{sale.id}.pdf")

@login_required
def export_consolidated_report(request):
    sales = Sale.objects.all().order_by('-date')
    buffer = generate_total_sales_report(sales)
    return FileResponse(buffer, as_attachment=True, filename="Reporte_Ventas_ImpulsoSmart.pdf")

@login_required
def pos_view(request):
    products = Product.objects.filter(stock__gt=0)
    customers = Customer.objects.all()
    default_customer = Customer.objects.filter(dni_cuit='00000000').first()
    last_sale_id = request.session.pop('last_sale_id', None)

    if request.method == 'POST':
        cart_data = request.POST.get('cart_data')
        customer_id = request.POST.get('customer_id')
        payment_method = request.POST.get('payment_method', 'CASH')
        
        try:
            cart = json.loads(cart_data)
            if not cart:
                messages.error(request, "El carrito está vacío")
                return redirect('sales:pos')

            customer = Customer.objects.get(id=customer_id) if customer_id else default_customer

            with transaction.atomic():
                sale = Sale.objects.create(
                    user=request.user,
                    customer=customer,
                    total_amount=0,
                    payment_method=payment_method
                )
                total = 0
                for item in cart:
                    product = Product.objects.select_for_update().get(id=item['id'])
                    qty = int(item['qty'])
                    if product.stock < qty:
                        raise Exception(f"Stock insuficiente para {product.name}")
                    
                    product.stock -= qty
                    product.save()
                    
                    subtotal = product.price * qty
                    total += subtotal
                    
                    SaleDetail.objects.create(
                        sale=sale,
                        product=product,
                        quantity=qty,
                        price=product.price,
                        subtotal=subtotal
                    )
                    
                    InventoryMovement.objects.create(
                        product=product,
                        quantity=qty,
                        movement_type='OUT',
                        reference=f"Venta #{sale.id}",
                        user=request.user
                    )
                
                sale.total_amount = total
                sale.save()
                
                if payment_method == 'CC':
                    customer.balance += total
                    customer.save()
                
                request.session['last_sale_id'] = sale.id
                messages.success(request, f"Venta #{sale.id} registrada ({sale.get_payment_method_display()}).")
                return redirect('sales:pos')
                
        except Exception as e:
            messages.error(request, f"Error al procesar venta: {str(e)}")
            return redirect('sales:pos')
            
    return render(request, 'sales/pos.html', {
        'products': products,
        'customers': customers,
        'default_customer': default_customer,
        'last_sale_id': last_sale_id
    })

@login_required
def sale_list(request):
    sales = Sale.objects.all().order_by('-date')
    return render(request, 'sales/sale_list.html', {'sales': sales})
