"""Clearly labelled demo catalog (is_demo=True). Removable via DELETE /api/catalog/demo."""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from ...core.services import SYSTEM
from ...seed import seed_hook
from .models import Brand, Category, Product

BRANDS = ["Demo Atelier", "Demo Studio", "Demo Works"]
CATEGORIES = [("Apparel", "Everyday clothing."), ("Home & living", "Pieces for the table and the room."),
              ("Accessories", "Bags and small goods."), ("Electronics", "Audio and lighting.")]

SIZES = ["S", "M", "L", "XL"]
# slug, title, category, brand, price (taka), compare-at (taka), options, description, stock per variant
PRODUCTS = [
    ("everyday-cotton-tee", "Everyday Cotton Tee", "Apparel", "Demo Atelier", 890, None,
     [("Size", ["S", "M", "L"]), ("Colour", ["Sand", "Ink"])],
     "<p>A mid-weight cotton tee cut for everyday wear.</p><ul><li>180 gsm combed cotton</li><li>Pre-washed</li></ul>", 12),
    ("linen-camp-shirt", "Linen Camp Shirt", "Apparel", "Demo Atelier", 1890, 2290,
     [("Size", SIZES)], "<p>A breathable camp-collar shirt in washed linen.</p>", 8),
    ("cotton-panjabi", "Cotton Panjabi", "Apparel", "Demo Studio", 2450, None,
     [("Size", ["M", "L", "XL"]), ("Colour", ["Indigo"])],
     "<p>A classic straight-cut panjabi with a placket and side slits.</p>", 6),
    ("pour-over-set", "Ceramic Pour-over Set", "Home & living", "Demo Works", 2200, None, [],
     "<p>Dripper, server and two cups in glazed ceramic.</p>", 15),
    ("stoneware-mug", "Stoneware Mug", "Home & living", "Demo Works", 640, None,
     [("Colour", ["Clay", "Sage"])], "<p>A hand-sized mug in matte stoneware. Dishwasher safe.</p>", 30),
    ("jute-basket", "Woven Jute Basket", "Home & living", "Demo Studio", 1150, None,
     [("Size", ["Small", "Large"])], "<p>Tightly woven jute basket with a sturdy handle.</p>", 10),
    ("card-holder", "Leather Card Holder", "Accessories", "Demo Atelier", 990, None,
     [("Colour", ["Tan", "Black"])], "<p>Slim card holder in vegetable-tanned leather.</p>", 20),
    ("canvas-tote", "Canvas Tote Bag", "Accessories", "Demo Studio", 750, 950, [],
     "<p>Heavy canvas tote with an inside pocket.</p>", 25),
    ("wireless-earbuds", "Wireless Earbuds", "Electronics", "Demo Works", 3490, None,
     [("Colour", ["Black", "White"])], "<p>Bluetooth earbuds with a charging case.</p>", 9),
    ("desk-lamp", "Articulated Desk Lamp", "Electronics", "Demo Works", 2990, None, [],
     "<p>Adjustable arm lamp. Shown as sold out to demonstrate stock states.</p>", 0),
]


@seed_hook(30, "catalog_demo")
def seed_demo_catalog(db, store, demo: bool):
    if not demo:
        return {"demo_products": 0}
    from ..inventory.ledger import apply_movement
    from ..inventory.models import Warehouse
    from .products import MediaIn, OptionIn, ProductIn, VariantIn, generate_matrix, save_product, validate_options
    from .service import slugify

    if db.execute(select(Product.id).where(Product.store_id == store.id, Product.is_demo.is_(True)).limit(1)).first():
        return {"demo_products": 0, "note": "already seeded"}
    wh = db.execute(select(Warehouse).where(Warehouse.store_id == store.id, Warehouse.is_default.is_(True))).scalar_one()

    system = SimpleNamespace(store=store, actor=SYSTEM)  # duck-typed principal for audit/events

    brands = {}
    for name in BRANDS:
        b = Brand(store_id=store.id, name=name, slug=slugify(name), is_demo=True)
        db.add(b)
        brands[name] = b
    cats = {}
    for i, (name, desc) in enumerate(CATEGORIES):
        c = Category(store_id=store.id, name=name, slug=slugify(name), description=desc, position=i, is_demo=True)
        db.add(c)
        cats[name] = c
    db.flush()
    n = 0
    for slug, title, cat, brand, price, compare, options, desc, stock in PRODUCTS:
        opts = validate_options([OptionIn(name=k, values=v) for k, v in options])
        variants = []
        for combo in generate_matrix(opts):
            suffix = "-".join(v[:3].upper() for v in combo.values())
            variants.append(VariantIn(sku=f"DEMO-{slug[:12].upper()}" + (f"-{suffix}" if suffix else ""),
                                      option_values=combo, price_minor=price * 100,
                                      compare_at_minor=compare * 100 if compare else None,
                                      cost_minor=int(price * 100 * 0.55)))
        body = ProductIn(title=title, slug=slug, description=desc, status="active", brand_id=brands[brand].id,
                         tags=["demo", cat.lower().split()[0]], options=[OptionIn(**o) for o in opts],
                         category_ids=[cats[cat].id], variants=variants,
                         media=[MediaIn(url=f"/demo/{slug}.svg", alt=f"{title} (demo illustration)")])
        pr = save_product(db, system, body, is_demo=True)  # type: ignore[arg-type]
        if stock:
            from .models import Variant
            for v in db.execute(select(Variant).where(Variant.product_id == pr.id)).scalars():
                apply_movement(db, store_id=store.id, variant_id=v.id, warehouse_id=wh.id, reason="receive",
                               delta_on_hand=stock, note="Demo opening stock")
        n += 1
    return {"demo_products": n}
