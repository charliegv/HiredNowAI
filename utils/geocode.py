import requests


def geocode_city(city: str, country: str):
	"""
	Returns (latitude, longitude) for a given city and country.
	Uses the free OpenStreetMap Nominatim API.
	"""

	if not city or not country:
		return None, None

	query = f"{city}, {country}"

	url = "https://nominatim.openstreetmap.org/search"

	params = {
		"q": query,
		"format": "json",
		"addressdetails": 0,
		"limit": 1
	}

	headers = {
		"User-Agent": "HiredNowAI/1.0 (charlie@hirednowai.com)"
	}

	try:
		r = requests.get(url, params=params, headers=headers, timeout=5)
		r.raise_for_status()
		results = r.json()

		if results:
			lat = float(results[0]["lat"])
			lon = float(results[0]["lon"])
			return lat, lon

	except Exception as e:
		print("Geocoding error:", e)

	return None, None
