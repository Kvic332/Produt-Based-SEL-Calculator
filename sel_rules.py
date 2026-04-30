import math


MONTH_ABBR = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


def ym_label(ym: str) -> str:
    year, month = ym.split("-")
    return f"{MONTH_ABBR[int(month)]} {year[-2:]}"


def get_interest_rate(amount: float, location: str, product_type: str) -> float | None:
    if location == "Expansion":
        if product_type == "RENEWAL":
            if 100000 < amount <= 199000:
                return 0.1894
            if 200000 <= amount <= 299000:
                return 0.1605
            if 300000 <= amount <= 409000:
                return 0.1426
            if 410000 <= amount <= 619000:
                return 0.1426
            if 620000 <= amount <= 749000:
                return 0.1190
            if 750000 <= amount <= 919000:
                return 0.1216
            if 920000 <= amount <= 1199000:
                return 0.1132
            if amount >= 1200000:
                return 0.1101
        else:
            if 100000 < amount <= 199000:
                return 0.3507
            if 200000 <= amount <= 299000:
                return 0.1997
            if 300000 <= amount <= 409000:
                return 0.1785
            if 410000 <= amount <= 619000:
                return 0.1847
            if 620000 <= amount <= 749000:
                return 0.1620
            if 750000 <= amount <= 919000:
                return 0.1720
            if 920000 <= amount <= 1199000:
                return 0.1573
            if amount >= 1200000:
                return 0.1480

    if product_type == "NTB":
        if location == "Lagos":
            if 100000 < amount <= 199000:
                return 0.3061
            if 200000 <= amount <= 919000:
                return 0.1385
            if 920000 <= amount <= 1199000:
                return 0.1264
            if amount >= 1200000:
                return 0.1076
        else:
            if 100000 < amount <= 199000:
                return 0.3630
            if 200000 <= amount <= 299000:
                return 0.1810
            if 300000 <= amount <= 749000:
                return 0.1579
            if 750000 <= amount <= 919000:
                return 0.1454
            if 920000 <= amount <= 1199000:
                return 0.1364
            if amount >= 1200000:
                return 0.1076
    else:
        if 100000 < amount <= 199000:
            return 0.1630
        if 200000 <= amount <= 919000:
            return 0.0934
        if 920000 <= amount <= 1199000:
            return 0.0769
        if amount >= 1200000:
            return 0.0941

    return None


def get_dti(total_net: float, product_type: str, location: str) -> float:
    if location == "Expansion" and product_type == "NTB":
        return 0.06
    if location == "Expansion" and product_type in {"RENEWAL", "TOP-UP"}:
        return 0.08
    if product_type == "NTB":
        return 0.07
    if product_type in {"RENEWAL", "TOP-UP"}:
        return 0.10 if total_net >= 801000 else 0.0
    return 0.0


def applicable_turnover(nets: list[float], product_type: str) -> float:
    total = sum(nets)
    average = total / len(nets) if nets else 0
    if product_type == "NTB" and len(nets) >= 3:
        trimmed = (total - min(nets) - max(nets)) / (len(nets) - 2)
        return min(average, trimmed)
    return average


def pv_calc(rate: float, tenor: int, payment: float) -> float:
    if rate == 0:
        return payment * tenor
    return payment * (1 - math.pow(1 + rate, -tenor)) / rate


def round_up_1000(value: float) -> int:
    return int(math.ceil(value / 1000) * 1000)


def loan_limits(location: str, product_type: str) -> tuple[int, int]:
    min_loan = 200000 if location == "Expansion" else 100000 if location == "Lagos" else 200000 if product_type == "NTB" else 500000
    max_loan = 3000000 if location == "Expansion" and product_type == "NTB" else 5000000 if location == "Expansion" else 10000000 if location == "Lagos" else 7000000
    return min_loan, max_loan


def apply_loan_limits(pv: float, location: str, product_type: str) -> int:
    min_loan, max_loan = loan_limits(location, product_type)
    if min_loan <= pv <= max_loan:
        return round_up_1000(pv)
    if pv > max_loan:
        return max_loan
    return 0


def calculate_eligibility(
    nets: list[float],
    counts: list[int],
    location: str,
    product_type: str,
    tenor: int,
    other_loans: float = 0,
    requested_loan: float = 0,
    manual_rate_percent: float | None = None,
) -> dict:
    total_net = sum(nets)
    average_count = sum(counts) / len(counts) if counts else 0
    # If ANY month has fewer than 12 transactions, use Monthly repayment
    any_month_below = counts and min(counts) < 12
    repayment_frequency = "Weekly" if (not any_month_below and average_count >= 12) else "Monthly"
    turnover = applicable_turnover(nets, product_type)
    dti = get_dti(total_net, product_type, location)
    max_repayment_monthly = max((turnover * dti) - other_loans, 0)
    max_total_repayment = max_repayment_monthly * tenor

    manual_rate = manual_rate_percent / 100 if manual_rate_percent and manual_rate_percent > 0 else None
    rate = manual_rate or get_interest_rate(max_total_repayment, location, product_type)
    new_loan = 0
    if rate and max_repayment_monthly > 0:
        new_loan = apply_loan_limits(pv_calc(rate, tenor, max_repayment_monthly), location, product_type)
        if not manual_rate and new_loan > 0:
            rate = get_interest_rate(new_loan, location, product_type)

    min_loan, max_loan = loan_limits(location, product_type)
    approved = min_loan <= new_loan <= max_loan
    weekly_payment = max_repayment_monthly / 4.33333

    result = {
        "total_net": total_net,
        "applicable_turnover": turnover,
        "dti": dti,
        "max_repayment_monthly": max_repayment_monthly,
        "max_repayment_display": weekly_payment if repayment_frequency == "Weekly" else max_repayment_monthly,
        "max_total_repayment": max_total_repayment,
        "repayment_frequency": repayment_frequency,
        "interest_rate": rate,
        "max_loan": new_loan,
        "decision": "Max loan amount" if approved else "Below product minimum",
    }

    if requested_loan:
        request_rate = manual_rate or get_interest_rate(requested_loan, location, product_type) or 0
        if request_rate > 0:
            request_payment = requested_loan * request_rate / (1 - math.pow(1 + request_rate, -tenor))
        else:
            request_payment = requested_loan / tenor
        result["requested"] = {
            "amount": requested_loan,
            "rate": request_rate,
            "repayment": request_payment / 4.33333 if repayment_frequency == "Weekly" else request_payment,
            "dti": (request_payment + other_loans) / turnover if turnover else 0,
            "within_max": requested_loan <= new_loan,
        }

    return result
