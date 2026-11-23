"""Fixed vocabularies and the starting lists for the CRM module."""

LEAD_STATUSES = [
    ("new", "New"), ("contacted", "Contacted"), ("qualified", "Qualified"), ("nurturing", "Nurturing"),
    ("unqualified", "Unqualified"), ("converted", "Converted"),
]
RATINGS = [("hot", "Hot"), ("warm", "Warm"), ("cold", "Cold")]
ACTIVITY_TYPES = [("task", "Task"), ("call", "Call"), ("meeting", "Meeting"), ("note", "Note"), ("email", "Email (logged)")]
PRIORITIES = [("low", "Low"), ("normal", "Normal"), ("high", "High")]
DEAL_STATUSES = [("open", "Open"), ("won", "Won"), ("lost", "Lost")]

# Suggestions for the free-text product interest field.
PRODUCT_INTERESTS = ["IT solutions", "Nano-tech services", "Consulting", "Software development", "Support and maintenance", "Training"]

# (key, label): the lead sources every company starts with. Companies can add more in CRM settings.
DEFAULT_SOURCES = [
    ("website", "Website"), ("referral", "Referral"), ("cold_call", "Cold call"), ("event", "Event"),
    ("social", "Social media"), ("partner", "Partner"), ("other", "Other"),
]

# (name, probability %, colour, kind). Kind: open, won or lost.
DEFAULT_STAGES = [
    ("Qualification", 10, "#8b8d94", "open"),
    ("Needs Analysis", 25, "#3f4a5a", "open"),
    ("Proposal/Quotation", 50, "#c9a24b", "open"),
    ("Negotiation", 75, "#8a6618", "open"),
    ("Closed Won", 100, "#1d6b42", "won"),
    ("Closed Lost", 0, "#b3281f", "lost"),
]

DEFAULT_LOST_REASONS = [
    "Price too high", "Chose a competitor", "No budget", "No response from the customer", "Requirements changed", "Timing not right",
]

# Lead scoring (see services/leads.py `score`): points per source key; any other source earns OTHER_SOURCE_POINTS.
SOURCE_POINTS = {"referral": 25, "partner": 20, "event": 15, "website": 12, "social": 8, "cold_call": 5, "other": 3}
OTHER_SOURCE_POINTS = 5
# (minimum estimated value in rupees, points), best match wins.
VALUE_POINTS = [(1_000_000, 30), (500_000, 20), (100_000, 10), (1, 5)]
ENGAGEMENT_POINTS_EACH = 5   # per completed call, meeting or logged email
ENGAGEMENT_MAX = 30
CONTACT_POINTS = 5           # for an email, and again for a phone number
HOT_SCORE = 60
WARM_SCORE = 30
