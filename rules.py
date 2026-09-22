"""Rule-based compliance checks for DRISHTI."""

import re


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _has_price(value: str) -> bool:
    value = _clean(value)

    return bool(
        re.search(
            r"(?:₹|rs\.?|inr)\s*[0-9]{1,7}(?:\.\d+)?",
            value,
            re.I,
        )
    ) or bool(
        re.fullmatch(
            r"(?:₹|rs\.?|inr)?\s*[0-9]{1,7}(?:\.\d+)?"
            r"(?:\s*(?:/-|only))?",
            value,
            re.I,
        )
    )


def _extract_number(value):
    match = re.search(
        r"([0-9]+(?:\.[0-9]+)?)",
        _clean(value),
    )

    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_unit(value):
    value = _clean(value).lower()

    match = re.search(
        r"(kg|g|mg|l|ml|litre|liter|pcs|pc|pieces|units?)\b",
        value,
    )

    return match.group(1) if match else None


def _extract_price(value):
    return _extract_number(value)


def _has_email(value):
    return bool(
        re.search(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            _clean(value),
        )
    )


def _has_phone(value):
    return bool(
        re.search(
            r"(?:\+91[\s-]?)?[6-9][0-9]{2}[\s-]?[0-9]{3}[\s-]?[0-9]{4}",
            _clean(value),
        )
    )


def _calculate_unit_price(net_quantity, mrp):
    quantity = _extract_number(net_quantity)
    unit = _extract_unit(net_quantity)
    price = _extract_price(mrp)

    if quantity is None or quantity <= 0 or price is None:
        return None

    unit = unit.lower()

    # Rule 6(11):
    # mass below 1 kg -> price per gram
    # mass 1 kg or more -> price per kg
    if unit == "mg":
        grams = quantity / 1000
        if grams <= 0:
            return None

        if grams < 1000:
            return price / grams
        return price / (grams / 1000)

    if unit == "g":
        if quantity < 1000:
            return price / quantity
        return price / 1000

    if unit == "kg":
        if quantity < 1:
            return price / (quantity * 1000)
        return price / quantity

    # Volume:
    # below 1 litre -> price per ml
    # 1 litre or more -> price per litre
    if unit == "ml":
        if quantity < 1000:
            return price / quantity
        return price / 1000

    if unit in {"l", "litre", "liter"}:
        if quantity < 1:
            return price / (quantity * 1000)
        return price / quantity

    # Number based packages
    if unit in {"pc", "pcs", "pieces", "unit", "units"}:
        return price / quantity

    return None


def check_compliance(fields, ocr_data=None, context=None):
    fields = fields or {}
    ocr_data = ocr_data or []
    context = context or {}

    checks = []

    imported = bool(context.get("imported", False))
    category = str(context.get("category", "general")).lower()

    # ============================================================
    # WEIGHTED SCORING
    # ============================================================

    weights = {
        "Product Name": 15,
        "Manufacturer/Packer": 15,
        "Country of Origin": 10,
        "Net Quantity": 15,
        "MRP": 15,
        "Unit Sale Price": 5,
        "Manufacturing/Packing Date": 10,
        "Best Before / Use By": 3,
        "Consumer Care Details": 10,
        "Legibility / Prominence": 2,
    }

    # ------------------------------------------------------------
    # PRODUCT NAME
    # ------------------------------------------------------------

    if _clean(fields.get("product_name")):
        checks.append({
            "field": "Product Name",
            "status": "PASS",
            "reason": "Product name/generic name was confirmed by the officer.",
            "weight": weights["Product Name"],
        })
    else:
        checks.append({
            "field": "Product Name",
            "status": "POSSIBLE NON-COMPLIANCE",
            "reason": "Product name/generic name is missing after officer verification.",
            "weight": weights["Product Name"],
        })

    # ------------------------------------------------------------
    # MANUFACTURER / PACKER
    # ------------------------------------------------------------

    if _clean(fields.get("manufacturer")):
        checks.append({
            "field": "Manufacturer/Packer",
            "status": "PASS",
            "reason": "Manufacturer/packer declaration was confirmed.",
            "weight": weights["Manufacturer/Packer"],
        })
    else:
        checks.append({
            "field": "Manufacturer/Packer",
            "status": "POSSIBLE NON-COMPLIANCE",
            "reason": "Manufacturer/packer declaration is missing after officer verification.",
            "weight": weights["Manufacturer/Packer"],
        })

    # ------------------------------------------------------------
    # COUNTRY OF ORIGIN
    # Applicable only to imported packages.
    # ------------------------------------------------------------

    if imported:
        if _clean(fields.get("country_of_origin")):
            checks.append({
                "field": "Country of Origin",
                "status": "PASS",
                "reason": "Country of origin was confirmed for the imported package.",
                "weight": weights["Country of Origin"],
            })
        else:
            checks.append({
                "field": "Country of Origin",
                "status": "POSSIBLE NON-COMPLIANCE",
                "reason": "Country of origin is required for the imported package but was not confirmed.",
                "weight": weights["Country of Origin"],
            })

    # ------------------------------------------------------------
    # NET QUANTITY
    # ------------------------------------------------------------

    if _clean(fields.get("net_quantity")):
        checks.append({
            "field": "Net Quantity",
            "status": "PASS",
            "reason": "Net quantity declaration was confirmed.",
            "weight": weights["Net Quantity"],
        })
    else:
        checks.append({
            "field": "Net Quantity",
            "status": "POSSIBLE NON-COMPLIANCE",
            "reason": "Net quantity declaration is missing after officer verification.",
            "weight": weights["Net Quantity"],
        })

    # ------------------------------------------------------------
    # MRP
    # ------------------------------------------------------------

    mrp = _clean(fields.get("mrp"))

    if mrp and _has_price(mrp):
        checks.append({
            "field": "MRP",
            "status": "PASS",
            "reason": "Maximum Retail Price was confirmed in a valid price format.",
            "weight": weights["MRP"],
        })
    elif mrp:
        checks.append({
            "field": "MRP",
            "status": "POSSIBLE NON-COMPLIANCE",
            "reason": "MRP was entered but its price format could not be validated.",
            "weight": weights["MRP"],
        })
    else:
        checks.append({
            "field": "MRP",
            "status": "POSSIBLE NON-COMPLIANCE",
            "reason": "MRP is missing after officer verification.",
            "weight": weights["MRP"],
        })

    # ------------------------------------------------------------
    # UNIT SALE PRICE
    # Only score when it has been supplied/verified.
    # ------------------------------------------------------------

    unit_price = _clean(fields.get("unit_sale_price"))

    if unit_price:
        checks.append({
            "field": "Unit Sale Price",
            "status": "PASS",
            "reason": "Unit sale price was confirmed by the officer.",
            "weight": weights["Unit Sale Price"],
        })

    # ------------------------------------------------------------
    # MANUFACTURING / PACKING DATE
    # ------------------------------------------------------------

    if _clean(fields.get("manufacturing_date")):
        checks.append({
            "field": "Manufacturing/Packing Date",
            "status": "PASS",
            "reason": "Manufacturing/packing date was confirmed.",
            "weight": weights["Manufacturing/Packing Date"],
        })
    else:
        checks.append({
            "field": "Manufacturing/Packing Date",
            "status": "POSSIBLE NON-COMPLIANCE",
            "reason": "Manufacturing/packing date is missing after officer verification.",
            "weight": weights["Manufacturing/Packing Date"],
        })

    # ------------------------------------------------------------
    # BEST BEFORE / USE BY
    # Food-category dependent.
    # ------------------------------------------------------------

    if category in {
        "food",
        "food product",
        "food products",
        "edible",
        "beverage",
        "packaged food",
    }:
        best_before = _clean(fields.get("best_before"))

        if best_before:
            checks.append({
                "field": "Best Before / Use By",
                "status": "PASS",
                "reason": "Best before/use by information was confirmed.",
                "weight": weights["Best Before / Use By"],
            })
        else:
            checks.append({
                "field": "Best Before / Use By",
                "status": "POSSIBLE NON-COMPLIANCE",
                "reason": "Best before/use by information was not confirmed for the food product.",
                "weight": weights["Best Before / Use By"],
            })

    # ------------------------------------------------------------
    # CONSUMER CARE
    # ------------------------------------------------------------

    if _clean(fields.get("consumer_care")):
        checks.append({
            "field": "Consumer Care Details",
            "status": "PASS",
            "reason": "Consumer care contact details were confirmed.",
            "weight": weights["Consumer Care Details"],
        })
    else:
        checks.append({
            "field": "Consumer Care Details",
            "status": "POSSIBLE NON-COMPLIANCE",
            "reason": "Consumer care details are missing after officer verification.",
            "weight": weights["Consumer Care Details"],
        })

    # ------------------------------------------------------------
    # LEGIBILITY / PROMINENCE
    # Officer must visually verify this.
    # We don't automatically deduct it.
    # ------------------------------------------------------------

    checks.append({
        "field": "Legibility / Prominence",
        "status": "NEEDS REVIEW",
        "reason": "Visual legibility and prominence require officer verification.",
        "weight": weights["Legibility / Prominence"],
    })

        # ============================================================
    # FINAL SCORE
    # ============================================================

    # "N/A" means the officer explicitly confirmed that the field
    # is not applicable. It must not be counted in the denominator.
    applicable_checks = [
        check
        for check in checks
        if _clean(fields.get({
            "Product Name": "product_name",
            "Manufacturer/Packer": "manufacturer",
            "Country of Origin": "country_of_origin",
            "Net Quantity": "net_quantity",
            "MRP": "mrp",
            "Unit Sale Price": "unit_sale_price",
            "Manufacturing/Packing Date": "manufacturing_date",
            "Best Before / Use By": "best_before",
            "Consumer Care Details": "consumer_care",
        }.get(check.get("field"), ""))) != "N/A"
    ]

    applicable_points = sum(
        check.get("weight", 0)
        for check in applicable_checks
    )

    earned_points = sum(
        check.get("weight", 0)
        for check in applicable_checks
        if check.get("status") == "PASS"
    )

    score = (
        round((earned_points / applicable_points) * 100)
        if applicable_points
        else 100
    )

    # ============================================================
    # FINAL STATUS
    # ============================================================

    has_failure = any(
        check.get("status") == "POSSIBLE NON-COMPLIANCE"
        for check in applicable_checks
    )

    has_review = any(
        check.get("status") == "NEEDS REVIEW"
        for check in applicable_checks
    )

    if score >= 90:
        overall_status = "HIGHLY COMPLIANT"
    elif score >= 75:
        overall_status = "COMPLIANT WITH MINOR ISSUES"
    elif score >= 50:
        overall_status = "PARTIALLY COMPLIANT"
    else:
        overall_status = "NON-COMPLIANT"

    # DRISHTI project classification, not statutory classification.
    if score >= 90:
        score_classification = "Highly Compliant"
    elif score >= 75:
        score_classification = "Compliant with Minor Issues"
    elif score >= 50:
        score_classification = "Partially Compliant"
    else:
        score_classification = "Non-Compliant"

    return {
        "overall_status": overall_status,
        "score": score,
        "score_out_of": 100,
        "score_classification": score_classification,
        "earned_points": earned_points,
        "applicable_points": applicable_points,
        "checks": checks,
        "disclaimer": (
            "This score is a DRISHTI project scoring aid based on "
            "officer-confirmed package declarations. It is not a "
            "statutory Legal Metrology compliance classification."
        ),
    }