from datetime import date
from random import Random

from app.services.demo_company_generator.models import (
    DemoAddress,
    DemoCompany,
    DemoContact,
    utc_now,
)


def build_apex_concrete_company(seed=123):
    random = Random(seed)
    suffix = random.randint(1000, 9999)

    return DemoCompany(
        legal_name="Apex Concrete LLC",
        dba_name="Apex Concrete",
        trade="Concrete",
        address=DemoAddress(
            street="1840 Founders Yard",
            city="Dallas",
            state="TX",
            zip_code="75201",
        ),
        phone="(214) 555-0187",
        email="michael.carter@apexconcrete-demo.com",
        website="https://apexconcrete-demo.com",
        ein=f"12-345{suffix}",
        license_number=f"TX-CONC-{suffix}",
        license_expiration=date(2027, 12, 31),
        years_in_business=18,
        employee_count=86,
        primary_contact=DemoContact(
            name="Michael Carter",
            title="President",
            email="michael.carter@apexconcrete-demo.com",
            phone="(214) 555-0187",
        ),
        safety_contact=DemoContact(
            name="Dana Ruiz",
            title="Safety Director",
            email="safety@apexconcrete-demo.com",
            phone="(214) 555-0194",
        ),
        emr=0.82,
        naics_code="238110",
        generated_at=utc_now(),
    )
