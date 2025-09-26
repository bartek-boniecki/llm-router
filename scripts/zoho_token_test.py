from api.integrations.zoho_recruit import create_candidate_from_email
out = create_candidate_from_email(
    email_from='token.test@example.com',
    subject='Token Test — Please ignore',
    body_preview='This is a token verification candidate.'
)
print(out)
