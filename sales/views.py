from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from products.models import Product, InventoryMovement
from .models import Sale, SaleDetail
import json

@login_required
def pos_view(request):
    products = Product.objects.filter(stock__gt=0)
    if request.method == 'POST':
        # Expecting 'cart' in POST data as JSON
        cart_data = request.POST.get('cart_data')
        try:
            cart = json.loads(cart_data)
            if not cart:
                messages.error(request, "El carrito está vacío")
                return redirect('pos')

            with transaction.atomic():
                sale = Sale.objects.create(
                    user=request.user,
                    total_amount=0 # Calculate below
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
                    
                    # Record Inventory Exit
                    InventoryMovement.objects.create(
                        product=product,
                        quantity=qty,
                        movement_type='OUT',
                        reference=f"Venta #{sale.id}",
                        user=request.user
                    )
                
                sale.total_amount = total
                sale.save()
                messages.success(request, f"Venta registrada. Total: {total}")
                return redirect('pos')
                
        except Exception as e:
            messages.error(request, f"Error al procesar venta: {str(e)}")
            return redirect('pos')
            
    return render(request, 'sales/pos.html', {'products': products})

@login_required
def sale_list(request):
    sales = Sale.objects.all().order_by('-date')
    return render(request, 'sales/sale_list.html', {'sales': sales})
