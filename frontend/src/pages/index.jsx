import { useState } from 'react';
import { Link } from 'react-router-dom';
import LogoUploader from '../components/LogoUploader';
import ProfileEditor from '../components/ProfileEditor';
import { useAuth, useCompany } from '../context';
import { BASIC_FIELDS, LEGAL_FIELDS } from '../specs';
import { Notice, PageHeader, Tabs } from '../ui';
import { BankAccounts } from './Bank';
import { BrandingDesigner } from './Branding';
import ChangeHistory from './History';
import { AccessManager } from './Access';
import { InviteManager } from './Invite';
import Home from './Home';
import { DocumentsManager } from './Documents';
import { LettersManager } from './Letters';
import { MinutesManager } from './Minutes';
import { NumberingManager } from './Numbering';
import { OperationsManager } from './Operations';
import { QuotationsManager } from './Quotations';
import Overview from './Overview';
import { PoliciesManager } from './Policies';
import {
  AddressesSection, ContactsSection, FinancialYearsManager, LetterheadTemplatesSection, PartnersSection,
  RegistrationsSection, SignatoriesSection,
} from './sections';
import { GstCalculator, TaxSettings } from './Tax';

export function OverviewPage() {
  return (<><PageHeader title="Company Overview" description="The single source of truth for the company's identity, registrations and document settings." /><Overview /></>);
}

export function BasicInfoPage() {
  const { user } = useAuth();
  return (
    <>
      <PageHeader title="Basic Company Information" description="Names, contact details and defaults used across the ERP." />
      <ProfileEditor fields={BASIC_FIELDS} />
      <section className="card">
        <LogoUploader title="Company logo" path="/company/profile/logo/" canWrite={user.is_company_admin}
          note="PNG or JPEG. Used on letterheads unless a letterhead override is uploaded under Branding." />
      </section>
    </>
  );
}

export function LegalPage() {
  const { profile } = useCompany();
  return (
    <>
      <PageHeader title="Legal and Registration Details" description="Optional until applicable and verified. Registration numbers are never assumed or filled in for you." />
      <ProfileEditor fields={LEGAL_FIELDS}>
        {profile && (
          <Notice>
            Registered office: {profile.registered_office ? `${profile.registered_office.label}, ${profile.registered_office.city}` : 'not recorded'}.{' '}
            <Link to="/company/addresses">Manage addresses</Link>
          </Notice>
        )}
      </ProfileEditor>
      <RegistrationsSection />
    </>
  );
}

export function AddressesPage() {
  return (<><PageHeader title="Registered and Business Addresses" /><AddressesSection /></>);
}

export function PartnersPage() {
  const [tab, setTab] = useState('partners');
  return (
    <>
      <PageHeader title="Partners and Signatories" />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: 'partners', label: 'Partners' }, { key: 'signatories', label: 'Authorized signatories' }]} />
      {tab === 'partners' ? <PartnersSection /> : <SignatoriesSection />}
    </>
  );
}

export function ContactsPage() {
  return (<><PageHeader title="Contact Directory" /><ContactsSection /></>);
}

export function TaxPage() {
  const [tab, setTab] = useState('settings');
  return (
    <>
      <PageHeader title="Tax Configuration" description="GST treatment, tax codes and registrations. Nothing is assumed to apply until you enable it." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: 'settings', label: 'GST settings' }, { key: 'registrations', label: 'Registrations' }, { key: 'calculator', label: 'Calculator' }]} />
      {tab === 'settings' && <TaxSettings />}
      {tab === 'registrations' && <RegistrationsSection />}
      {tab === 'calculator' && <GstCalculator />}
    </>
  );
}

export function BankPage() {
  return (<><PageHeader title="Bank Accounts" description="Payment details are masked, access-controlled and audited." /><BankAccounts /></>);
}

export function BrandingPage() {
  return (<><PageHeader title="Branding and Letterhead Designer" description="Adjust the letterhead and see a live preview before saving." /><BrandingDesigner /></>);
}

export function NumberingPage() {
  return (<><PageHeader title="Document Numbering" /><NumberingManager /></>);
}

export function MinutesPage() {
  return (
    <>
      <PageHeader title="Meeting Minutes" description="Record a meeting, issue the minutes with a unique ID, and download them for printing." />
      <MinutesManager />
    </>
  );
}

export function LettersPage() {
  return (
    <>
      <PageHeader title="Letters and Documents" description="Write a letter, issue it with a unique ID, and download it for printing." />
      <LettersManager />
    </>
  );
}

export function HomePage() {
  return <Home />;
}

export function InvitePage() {
  return (
    <>
      <PageHeader title="Invite members" description="Send a one-time link so a new member can create their own account with the access role you choose." />
      <InviteManager />
    </>
  );
}

export function AccessPage() {
  return (
    <>
      <PageHeader title="Users and Access" description="Who can sign in, what each role may do, and a record of sign-ins and changes." />
      <AccessManager />
    </>
  );
}

export function QuotationsPage() {
  return (
    <>
      <PageHeader title="Quotations" description="Enter the customer and items, check the totals with GST, and issue a quotation with its own unique ID." />
      <QuotationsManager />
    </>
  );
}

export function OperationsPage() {
  return (
    <>
      <PageHeader title="Operations" description="Choose an operation, paste the content, add sections, and download the document with its own unique ID." />
      <OperationsManager />
    </>
  );
}

export function LetterheadTemplatesPage() {
  return (
    <>
      <PageHeader title="Letterhead Templates" description="The template catalogue (LHT-001 and so on) and the reference each one issues." />
      <LetterheadTemplatesSection />
    </>
  );
}

export function FinancialYearsPage() {
  return (<><PageHeader title="Financial Year Settings" /><FinancialYearsManager /></>);
}

export function PoliciesPage() {
  return (<><PageHeader title="Company Policies and Default Terms" /><PoliciesManager /></>);
}

export function DocumentsPage() {
  return (<><PageHeader title="Company Documents" /><DocumentsManager /></>);
}

export function HistoryPage() {
  return (<><PageHeader title="Company Change History" description="An append-only record of changes to company settings." /><ChangeHistory /></>);
}
