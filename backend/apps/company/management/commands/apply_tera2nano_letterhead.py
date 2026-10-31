"""Apply the Tera2Nano printed letterhead as company data.

The values below come from the company's printed letterhead ("letter pad t2n.pdf"): name, address,
phone, e-mail, web address and the visual layout. They are written to the database (with audit
events), not read from code at document time. Registration numbers, bank details and tax data are
deliberately not touched. The command only fills fields that are still blank and leaves branding
alone once it has been customised, unless --force is given.

    python manage.py apply_tera2nano_letterhead --logo blk.png --watermark clr.png

Partner names/titles/contacts come from the printed business cards, and both partners are recorded as
designated partners as confirmed by the company. No DPIN or registration numbers are stored: the cards
do not carry them.
"""
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.core.files import inspect_upload
from apps.core.images import prepare_logo
from apps.company.models import (
    CompanyAddress, CompanyBranding, CompanyContact, CompanyPartner, CompanyProfile, CompanyProfileAuditEvent,
)
from apps.company.services import audit, company as company_service, numbering, setup as setup_service

LEGAL_NAME = "TERA2NANO SOLUTIONS LLP"
DISPLAY_NAME = "Tera2Nano Solutions LLP"

PROFILE_FILL = {
    "short_code": "T2N",
    "legal_entity_type": CompanyProfile.EntityType.LLP,  # stated in the legal name
    "primary_email": "admin@tera2nano.com",
    "primary_phone": "+91 8220789878",
    "website": "https://www.tera2nano.com",
}

ADDRESS = dict(
    label="Letterhead address", address_type=CompanyAddress.AddressType.CORPORATE, building_name="",
    line1="No 14, M.F. Road", line2="IInd Cross, Kellys Road", locality="Ranipet", district="Walajapet Taluk",
    city="Vellore", state="Tamil Nadu", state_code="33", postal_code="632401", country="India", is_primary=True,
)

CONTACT = dict(contact_type="general", display_name="Tera2Nano Solutions LLP", email="admin@tera2nano.com",
               phone="+91 8220789878", department="Administration", is_primary=True)

# From the business cards. The cards carry a title and contact details; names are taken from the card file
# names and e-mail addresses, so please check them in Partners and Signatories.
PARTNERS = [
    dict(full_name="Delight Cherubino", designation="Founder & Managing Partner",
         official_email="delightcherubino@tera2nano.com", official_phone="+91 8220789878"),
    dict(full_name="Sanjay", designation="Co-Founder & Technical Partner",
         official_email="sanjay@tera2nano.com", official_phone="+91 85239 99020"),
]

# Matches the printed letterhead: logo and name top-left, centred address/contact footer,
# pale grey page and a faint logo watermark.
BRANDING = dict(
    default_template="footer_contact", phone_label="Mob", logo_position="left", logo_width_mm=32,
    header_alignment="left", footer_alignment="center", primary_colour="#304352", secondary_colour="#3B4F63",
    page_background_colour="#F5F5F3", watermark_enabled=True, watermark_type="logo", watermark_size_mm=100,
    watermark_opacity="0.25", margin_top_mm=10, margin_bottom_mm=8, margin_left_mm=18, margin_right_mm=18,
    header_height_mm=28, footer_height_mm=14, heading_font_size=14, footer_font_size=8, show_page_numbers=True,
    default_page_size="A4", line_spacing="1.45", paragraph_spacing_pt=8, body_top_gap_mm=4,
)


class Command(BaseCommand):
    help = "Apply the Tera2Nano letterhead (name, address, contacts and layout) to the company data."

    def add_arguments(self, parser):
        parser.add_argument("--logo", help="Logo for the letterhead header (outline version, e.g. blk.png).")
        parser.add_argument("--watermark", help="Image for the page watermark (e.g. the colour logo, clr.png).")
        parser.add_argument("--raw", action="store_true",
                            help="Use the images exactly as given (skip background removal and cropping).")
        parser.add_argument("--force", action="store_true", help="Overwrite branding even if it was customised.")

    @transaction.atomic
    def handle(self, *args, **opts):
        actor = User.objects.filter(is_active=True, role="admin").first() or User.objects.filter(
            is_active=True, is_superuser=True).first()
        company = CompanyProfile.objects.first()
        if company is None:
            company, _ = setup_service.start_setup(LEGAL_NAME, DISPLAY_NAME, actor)
            self.stdout.write(f"Created company profile '{company.display_name}'.")

        before = audit.snapshot(company)
        filled = [k for k, v in PROFILE_FILL.items() if not getattr(company, k)]
        for key in filled:
            setattr(company, key, PROFILE_FILL[key])
        if filled:
            company.full_clean()
            company.save()
            changed, prev, new = audit.diff("profile", before, audit.snapshot(company))
            audit.record_event(company, actor, "profile", company.id, CompanyProfileAuditEvent.Action.UPDATED,
                               changed=changed, previous=prev, new=new, reason="Applied letterhead details")
            self.stdout.write(f"Filled blank profile fields: {', '.join(filled)}.")

        if not CompanyAddress.objects.filter(company=company).exists():
            address = CompanyAddress(company=company, **ADDRESS)
            address.full_clean()
            address.save()
            audit.record_event(company, actor, "address", address.id, CompanyProfileAuditEvent.Action.CREATED,
                               changed=["label", "address_type", "city"], new={"label": address.label, "city": address.city},
                               reason="Applied letterhead details")
            self.stdout.write("Added the letterhead address.")
        else:
            self.stdout.write("Company already has an address; left unchanged.")

        branding = company_service.get_branding(company)
        if branding.version == 1 or opts["force"]:
            before = audit.snapshot(branding)
            for key, value in BRANDING.items():
                setattr(branding, key, value)
            branding.full_clean()
            branding.version += 1
            branding.save()
            changed, prev, new = audit.diff("branding", before, audit.snapshot(branding))
            audit.record_event(company, actor, "branding", branding.id, CompanyProfileAuditEvent.Action.UPDATED,
                               changed=changed, previous=prev, new=new, reason="Applied letterhead layout")
            self.stdout.write("Applied the letterhead layout.")
        else:
            self.stdout.write("Branding was already customised; left unchanged (use --force to overwrite).")

        if company.short_code:
            created = numbering.seed_default_sequences(company, company.short_code)
            for seq in created:
                audit.record_event(company, actor, "numbering", seq.pk, CompanyProfileAuditEvent.Action.CREATED,
                                   category="numbering", changed=["document_type", "prefix"],
                                   new={"document_type": seq.document_type, "prefix": seq.prefix},
                                   reason="Default sequences created")
            if created:
                self.stdout.write(f"Created {len(created)} default numbering sequences (existing ones untouched).")

        if not CompanyContact.objects.filter(company=company).exists():
            contact = CompanyContact(company=company, **CONTACT)
            contact.full_clean()
            contact.save()
            audit.record_event(company, actor, "contact", contact.id, CompanyProfileAuditEvent.Action.CREATED,
                               changed=["contact_type", "display_name"], new={"contact_type": "general"},
                               reason="Applied letterhead details")
            self.stdout.write("Added the general contact.")

        designated = dict(partner_type=CompanyPartner.PartnerType.DESIGNATED, is_designated_partner=True)
        for data in PARTNERS:
            partner = CompanyPartner.objects.filter(company=company, full_name=data["full_name"]).first()
            if partner is None:
                partner = CompanyPartner(company=company, **designated, **data)
                partner.full_clean()
                partner.save()
                audit.record_event(company, actor, "partner", partner.id, CompanyProfileAuditEvent.Action.CREATED,
                                   changed=["full_name", "designation", "is_designated_partner"],
                                   new={"full_name": partner.full_name, "is_designated_partner": True},
                                   reason="Applied business-card details; confirmed designated partner")
                self.stdout.write(f"Added designated partner {partner.full_name} ({partner.designation}).")
            elif not partner.is_designated_partner:
                # Partner added by an earlier run as an ordinary partner: record the confirmed status.
                before = audit.snapshot(partner)
                for key, value in designated.items():
                    setattr(partner, key, value)
                partner.full_clean()
                partner.save()
                changed, prev, new = audit.diff("partner", before, audit.snapshot(partner))
                audit.record_event(company, actor, "partner", partner.id, CompanyProfileAuditEvent.Action.UPDATED,
                                   changed=changed, previous=prev, new=new,
                                   reason="Confirmed by the company: designated partner")
                self.stdout.write(f"Marked {partner.full_name} as a designated partner.")

        if opts["logo"]:
            upload = self._image(opts["logo"], opts["raw"])
            if company.logo:
                company.logo.delete(save=False)
            company.logo.save("logo", upload.file, save=False)
            company.logo_checksum = upload.checksum
            company.save(update_fields=["logo", "logo_checksum", "updated_at"])
            audit.record_event(company, actor, "profile", company.id, CompanyProfileAuditEvent.Action.UPDATED,
                               changed=["logo"], previous={"logo": ""}, new={"logo": "set"}, category="branding",
                               reason="Applied letterhead logo")
            self.stdout.write("Stored the letterhead logo.")
        elif not company.logo:
            self.stdout.write(self.style.WARNING("No logo yet: re-run with --logo or upload it in the app."))

        if opts["watermark"]:
            upload = self._image(opts["watermark"], opts["raw"])
            if branding.watermark_image:
                branding.watermark_image.delete(save=False)
            branding.watermark_image.save("watermark", upload.file, save=False)
            branding.watermark_image_checksum = upload.checksum
            branding.version += 1
            branding.save()
            audit.record_event(company, actor, "branding", branding.id, CompanyProfileAuditEvent.Action.UPDATED,
                               changed=["watermark_image"], previous={"watermark_image": ""},
                               new={"watermark_image": "set"}, reason="Applied letterhead watermark")
            self.stdout.write("Stored the watermark image.")

    def _image(self, path, raw):
        from django.core.files.uploadedfile import SimpleUploadedFile
        path = Path(path)
        if not path.is_file():
            raise CommandError(f"Image not found: {path}")
        data = path.read_bytes()
        if not raw:
            data = prepare_logo(data)
        return inspect_upload(SimpleUploadedFile(path.stem + ".png" if not raw else path.name, data), images_only=True)
