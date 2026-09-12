#!/usr/bin/env python3
"""
ORA-COOL AI — phone number intelligence (OSINT).
Identifies country, region/state/city, carrier and line-type from a phone
number's metadata — the legit, PhoneInfoga-style approach. Exact live GPS
tracking is NOT possible for civilians (only telecoms/LE can do that).
"""
import json
import re
import urllib.parse
from zoneinfo import ZoneInfo

# country calling code -> (name, ISO)
COUNTRY_CODES = {
 "1": ("United States / Canada", "US"), "7": ("Russia / Kazakhstan", "RU"),
 "20": ("Egypt", "EG"), "27": ("South Africa", "ZA"), "30": ("Greece", "GR"),
 "31": ("Netherlands", "NL"), "32": ("Belgium", "BE"), "33": ("France", "FR"),
 "34": ("Spain", "ES"), "36": ("Hungary", "HU"), "39": ("Italy", "IT"),
 "40": ("Romania", "RO"), "41": ("Switzerland", "CH"), "43": ("Austria", "AT"),
 "44": ("United Kingdom", "GB"), "45": ("Denmark", "DK"), "46": ("Sweden", "SE"),
 "47": ("Norway", "NO"), "48": ("Poland", "PL"), "49": ("Germany", "DE"),
 "51": ("Peru", "PE"), "52": ("Mexico", "MX"), "53": ("Cuba", "CU"),
 "54": ("Argentina", "AR"), "55": ("Brazil", "BR"), "56": ("Chile", "CL"),
 "57": ("Colombia", "CO"), "58": ("Venezuela", "VE"), "60": ("Malaysia", "MY"),
 "61": ("Australia", "AU"), "62": ("Indonesia", "ID"), "63": ("Philippines", "PH"),
 "64": ("New Zealand", "NZ"), "65": ("Singapore", "SG"), "66": ("Thailand", "TH"),
 "81": ("Japan", "JP"), "82": ("South Korea", "KR"), "84": ("Vietnam", "VN"),
 "86": ("China", "CN"), "90": ("Turkey", "TR"), "91": ("India", "IN"),
 "92": ("Pakistan", "PK"), "93": ("Afghanistan", "AF"), "94": ("Sri Lanka", "LK"),
 "95": ("Myanmar", "MM"), "98": ("Iran", "IR"),
 "211": ("South Sudan", "SS"), "212": ("Morocco", "MA"), "213": ("Algeria", "DZ"),
 "216": ("Tunisia", "TN"), "218": ("Libya", "LY"), "220": ("Gambia", "GM"),
 "221": ("Senegal", "SN"), "222": ("Mauritania", "MR"), "223": ("Mali", "ML"),
 "224": ("Guinea", "GN"), "225": ("Ivory Coast", "CI"), "226": ("Burkina Faso", "BF"),
 "227": ("Niger", "NE"), "228": ("Togo", "TG"), "229": ("Benin", "BJ"),
 "230": ("Mauritius", "MU"), "231": ("Liberia", "LR"), "232": ("Sierra Leone", "SL"),
 "233": ("Ghana", "GH"), "234": ("Nigeria", "NG"), "235": ("Chad", "TD"),
 "236": ("Central African Republic", "CF"), "237": ("Cameroon", "CM"),
 "238": ("Cape Verde", "CV"), "239": ("São Tomé and Príncipe", "ST"),
 "240": ("Equatorial Guinea", "GQ"), "241": ("Gabon", "GA"), "242": ("Congo", "CG"),
 "243": ("DR Congo", "CD"), "244": ("Angola", "AO"), "245": ("Guinea-Bissau", "GW"),
 "248": ("Seychelles", "SC"), "249": ("Sudan", "SD"), "250": ("Rwanda", "RW"),
 "251": ("Ethiopia", "ET"), "252": ("Somalia", "SO"), "253": ("Djibouti", "DJ"),
 "254": ("Kenya", "KE"), "255": ("Tanzania", "TZ"), "256": ("Uganda", "UG"),
 "257": ("Burundi", "BI"), "258": ("Mozambique", "MZ"), "260": ("Zambia", "ZM"),
 "261": ("Madagascar", "MG"), "262": ("Réunion", "RE"), "263": ("Zimbabwe", "ZW"),
 "264": ("Namibia", "NA"), "265": ("Malawi", "MW"), "266": ("Lesotho", "LS"),
 "267": ("Botswana", "BW"), "268": ("Eswatini", "SZ"), "269": ("Comoros", "KM"),
 "291": ("Eritrea", "ER"), "297": ("Aruba", "AW"), "298": ("Faroe Islands", "FO"),
 "299": ("Greenland", "GL"),
 "350": ("Gibraltar", "GI"), "351": ("Portugal", "PT"), "352": ("Luxembourg", "LU"),
 "353": ("Ireland", "IE"), "354": ("Iceland", "IS"), "355": ("Albania", "AL"),
 "356": ("Malta", "MT"), "357": ("Cyprus", "CY"), "358": ("Finland", "FI"),
 "359": ("Bulgaria", "BG"), "370": ("Lithuania", "LT"), "371": ("Latvia", "LV"),
 "372": ("Estonia", "EE"), "373": ("Moldova", "MD"), "374": ("Armenia", "AM"),
 "375": ("Belarus", "BY"), "376": ("Andorra", "AD"), "377": ("Monaco", "MC"),
 "378": ("San Marino", "SM"), "380": ("Ukraine", "UA"), "381": ("Serbia", "RS"),
 "382": ("Montenegro", "ME"), "383": ("Kosovo", "XK"), "385": ("Croatia", "HR"),
 "386": ("Slovenia", "SI"), "387": ("Bosnia & Herzegovina", "BA"),
 "389": ("North Macedonia", "MK"), "420": ("Czechia", "CZ"), "421": ("Slovakia", "SK"),
 "423": ("Liechtenstein", "LI"), "500": ("Falkland Islands", "FK"),
 "501": ("Belize", "BZ"), "502": ("Guatemala", "GT"), "503": ("El Salvador", "SV"),
 "504": ("Honduras", "HN"), "505": ("Nicaragua", "NI"), "506": ("Costa Rica", "CR"),
 "507": ("Panama", "PA"), "509": ("Haiti", "HT"), "590": ("Guadeloupe", "GP"),
 "591": ("Bolivia", "BO"), "592": ("Guyana", "GY"), "593": ("Ecuador", "EC"),
 "594": ("French Guiana", "GF"), "595": ("Paraguay", "PY"), "596": ("Martinique", "MQ"),
 "597": ("Suriname", "SR"), "598": ("Uruguay", "UY"), "599": ("Curaçao", "CW"),
 "670": ("Timor-Leste", "TL"), "673": ("Brunei", "BN"),
 "675": ("Papua New Guinea", "PG"), "676": ("Tonga", "TO"),
 "677": ("Solomon Islands", "SB"), "678": ("Vanuatu", "VU"), "679": ("Fiji", "FJ"),
 "680": ("Palau", "PW"), "682": ("Cook Islands", "CK"), "685": ("Samoa", "WS"),
 "686": ("Kiribati", "KI"), "687": ("New Caledonia", "NC"), "688": ("Tuvalu", "TV"),
 "689": ("French Polynesia", "PF"), "690": ("Tokelau", "TK"),
 "691": ("Micronesia", "FM"), "692": ("Marshall Islands", "MH"),
 "850": ("North Korea", "KP"), "852": ("Hong Kong", "HK"), "853": ("Macau", "MO"),
 "855": ("Cambodia", "KH"), "856": ("Laos", "LA"), "880": ("Bangladesh", "BD"),
 "886": ("Taiwan", "TW"), "960": ("Maldives", "MV"), "961": ("Lebanon", "LB"),
 "962": ("Jordan", "JO"), "963": ("Syria", "SY"), "964": ("Iraq", "IQ"),
 "965": ("Kuwait", "KW"), "966": ("Saudi Arabia", "SA"), "967": ("Yemen", "YE"),
 "968": ("Oman", "OM"), "970": ("Palestine", "PS"), "971": ("United Arab Emirates", "AE"),
 "972": ("Israel", "IL"), "973": ("Bahrain", "BH"), "974": ("Qatar", "QA"),
 "975": ("Bhutan", "BT"), "976": ("Mongolia", "MN"), "977": ("Nepal", "NP"),
 "992": ("Tajikistan", "TJ"), "993": ("Turkmenistan", "TM"),
 "994": ("Azerbaijan", "AZ"), "995": ("Georgia", "GE"), "996": ("Kyrgyzstan", "KG"),
 "998": ("Uzbekistan", "UZ"),
}

COUNTRY_TZ = {
 "7": "Europe/Moscow", "20": "Africa/Cairo", "27": "Africa/Johannesburg",
 "30": "Europe/Athens", "31": "Europe/Amsterdam", "32": "Europe/Brussels",
 "33": "Europe/Paris", "34": "Europe/Madrid", "36": "Europe/Budapest",
 "39": "Europe/Rome", "40": "Europe/Bucharest", "41": "Europe/Zurich",
 "43": "Europe/Vienna", "44": "Europe/London", "45": "Europe/Copenhagen",
 "46": "Europe/Stockholm", "47": "Europe/Oslo", "48": "Europe/Warsaw",
 "49": "Europe/Berlin", "51": "America/Lima", "52": "America/Mexico_City",
 "54": "America/Argentina/Buenos_Aires", "55": "America/Sao_Paulo",
 "56": "America/Santiago", "57": "America/Bogota", "58": "America/Caracas",
 "60": "Asia/Kuala_Lumpur", "61": "Australia/Sydney", "62": "Asia/Jakarta",
 "63": "Asia/Manila", "64": "Pacific/Auckland", "65": "Asia/Singapore",
 "66": "Asia/Bangkok", "81": "Asia/Tokyo", "82": "Asia/Seoul",
 "84": "Asia/Ho_Chi_Minh", "86": "Asia/Shanghai", "90": "Europe/Istanbul",
 "91": "Asia/Kolkata", "92": "Asia/Karachi", "93": "Asia/Kabul",
 "94": "Asia/Colombo", "95": "Asia/Yangon", "98": "Asia/Tehran",
 "212": "Africa/Casablanca", "213": "Africa/Algiers", "216": "Africa/Tunis",
 "220": "Africa/Banjul", "221": "Africa/Dakar", "222": "Africa/Nouakchott",
 "223": "Africa/Bamako", "224": "Africa/Conakry", "225": "Africa/Abidjan",
 "226": "Africa/Ouagadougou", "227": "Africa/Niamey", "228": "Africa/Lome",
 "229": "Africa/Porto-Novo", "230": "Indian/Mauritius", "231": "Africa/Monrovia",
 "232": "Africa/Freetown", "233": "Africa/Accra", "234": "Africa/Lagos",
 "235": "Africa/Ndjamena", "236": "Africa/Bangui", "237": "Africa/Douala",
 "238": "Atlantic/Cape_Verde", "239": "Africa/Sao_Tome", "240": "Africa/Malabo",
 "241": "Africa/Libreville", "242": "Africa/Brazzaville", "243": "Africa/Kinshasa",
 "244": "Africa/Luanda", "245": "Africa/Bissau", "248": "Indian/Mahe",
 "249": "Africa/Khartoum", "250": "Africa/Kigali", "251": "Africa/Addis_Ababa",
 "252": "Africa/Mogadishu", "253": "Africa/Djibouti", "254": "Africa/Nairobi",
 "255": "Africa/Dar_es_Salaam", "256": "Africa/Kampala", "257": "Africa/Bujumbura",
 "258": "Africa/Maputo", "260": "Africa/Lusaka", "261": "Indian/Antananarivo",
 "263": "Africa/Harare", "264": "Africa/Windhoek", "265": "Africa/Blantyre",
 "266": "Africa/Maseru", "267": "Africa/Gaborone", "268": "Africa/Mbabane",
 "269": "Indian/Comoro", "291": "Africa/Asmara", "350": "Europe/Gibraltar",
 "351": "Europe/Lisbon", "352": "Europe/Luxembourg", "353": "Europe/Dublin",
 "354": "Atlantic/Reykjavik", "355": "Europe/Tirane", "356": "Europe/Malta",
 "357": "Asia/Nicosia", "358": "Europe/Helsinki", "359": "Europe/Sofia",
 "370": "Europe/Vilnius", "371": "Europe/Riga", "372": "Europe/Tallinn",
 "373": "Europe/Chisinau", "374": "Asia/Yerevan", "375": "Europe/Minsk",
 "376": "Europe/Andorra", "377": "Europe/Monaco", "378": "Europe/San_Marino",
 "380": "Europe/Kyiv", "381": "Europe/Belgrade", "382": "Europe/Podgorica",
 "385": "Europe/Zagreb", "386": "Europe/Ljubljana", "387": "Europe/Sarajevo",
 "389": "Europe/Skopje", "420": "Europe/Prague", "421": "Europe/Bratislava",
 "500": "Atlantic/Stanley", "501": "America/Belize", "502": "America/Guatemala",
 "503": "America/El_Salvador", "504": "America/Tegucigalpa", "505": "America/Managua",
 "506": "America/Costa_Rica", "507": "America/Panama", "509": "America/Port-au-Prince",
 "591": "America/La_Paz", "592": "America/Guyana", "593": "America/Guayaquil",
 "595": "America/Asuncion", "597": "America/Paramaribo", "598": "America/Montevideo",
 "670": "Asia/Dili", "673": "Asia/Brunei", "675": "Pacific/Port_Moresby",
 "676": "Pacific/Tongatapu", "678": "Pacific/Efate", "679": "Pacific/Fiji",
 "685": "Pacific/Apia", "686": "Pacific/Tarawa", "687": "Pacific/Noumea",
 "688": "Pacific/Funafuti", "689": "Pacific/Tahiti", "691": "Pacific/Chuuk",
 "692": "Pacific/Majuro", "852": "Asia/Hong_Kong", "853": "Asia/Macau",
 "855": "Asia/Phnom_Penh", "856": "Asia/Vientiane", "880": "Asia/Dhaka",
 "886": "Asia/Taipei", "960": "Indian/Maldives", "961": "Asia/Beirut",
 "962": "Asia/Amman", "963": "Asia/Damascus", "964": "Asia/Baghdad",
 "965": "Asia/Kuwait", "966": "Asia/Riyadh", "967": "Asia/Aden",
 "968": "Asia/Muscat", "970": "Asia/Gaza", "971": "Asia/Dubai",
 "972": "Asia/Jerusalem", "973": "Asia/Bahrain", "974": "Asia/Qatar",
 "975": "Asia/Thimphu", "976": "Asia/Ulaanbaatar", "977": "Asia/Kathmandu",
 "992": "Asia/Dushanbe", "993": "Asia/Ashgabat", "994": "Asia/Baku",
 "995": "Asia/Tbilisi", "996": "Asia/Bishkek", "998": "Asia/Tashkent",
}

# Nigeria mobile prefixes -> network
NGN_MOBILE = {
 "0803": "MTN", "0806": "MTN", "0703": "MTN", "0706": "MTN", "0813": "MTN",
 "0816": "MTN", "0810": "MTN", "0814": "MTN", "0903": "MTN", "0906": "MTN",
 "0913": "MTN", "0916": "MTN", "0704": "MTN", "07025": "MTN", "07026": "MTN",
 "0805": "Glo", "0807": "Glo", "0705": "Glo", "0815": "Glo", "0811": "Glo",
 "0905": "Glo", "0915": "Glo",
 "0802": "Airtel", "0808": "Airtel", "0708": "Airtel", "0812": "Airtel",
 "0701": "Airtel", "0902": "Airtel", "0901": "Airtel", "0904": "Airtel",
 "0907": "Airtel", "0912": "Airtel",
 "0809": "9mobile", "0818": "9mobile", "0817": "9mobile", "0909": "9mobile",
 "0908": "9mobile",
 "0804": "ntel", "0702": "Smile", "0819": "CDMA",
}
NGN_LANDLINE = {
 "01": "Lagos", "02": "Ibadan", "09": "Abuja (FCT)", "042": "Enugu",
 "052": "Benin City", "062": "Kaduna", "064": "Kano", "082": "Aba",
 "083": "Port Harcourt", "084": "Port Harcourt", "046": "Onitsha",
 "031": "Ijebu-Ode", "037": "Sagamu", "038": "Ota", "043": "Awka",
 "044": "Abakaliki", "047": "Nnewi", "048": "Owerri", "053": "Warri",
 "054": "Sapele", "055": "Ughelli", "056": "Asaba", "060": "Zaria",
 "061": "Jos", "065": "Sokoto", "066": "Gusau", "067": "Katsina",
 "068": "Birnin Kebbi", "069": "Dutse", "070": "Makurdi", "071": "Lokoja",
 "072": "Akure", "073": "Ondo", "074": "Minna", "075": "Bida",
 "076": "Ilorin", "077": "Ile-Ife", "078": "Ijero-Ekiti", "079": "Okene",
 "080": "Maiduguri", "081": "Yola", "085": "Bauchi", "086": "Gombe",
 "087": "Damaturu", "088": "Jalingo", "089": "Lafia",
}

# US area code -> state
US_AREA = {
 "205": "Alabama", "251": "Alabama", "256": "Alabama", "334": "Alabama",
 "659": "Alabama", "938": "Alabama", "907": "Alaska",
 "480": "Arizona", "520": "Arizona", "602": "Arizona", "623": "Arizona", "928": "Arizona",
 "479": "Arkansas", "501": "Arkansas", "870": "Arkansas",
 "209": "California", "213": "California", "279": "California",
 "310": "California", "323": "California", "341": "California",
 "408": "California", "415": "California", "424": "California", "442": "California",
 "510": "California", "530": "California", "559": "California", "562": "California",
 "619": "California", "626": "California", "628": "California", "650": "California",
 "657": "California", "661": "California", "669": "California", "707": "California",
 "714": "California", "747": "California", "760": "California", "805": "California",
 "818": "California", "820": "California", "831": "California", "858": "California",
 "909": "California", "916": "California", "925": "California", "949": "California",
 "951": "California",
 "303": "Colorado", "719": "Colorado", "720": "Colorado", "970": "Colorado",
 "203": "Connecticut", "475": "Connecticut", "860": "Connecticut", "959": "Connecticut",
 "302": "Delaware",
 "239": "Florida", "305": "Florida", "321": "Florida", "352": "Florida",
 "386": "Florida", "407": "Florida", "561": "Florida", "727": "Florida",
 "754": "Florida", "772": "Florida", "786": "Florida", "813": "Florida",
 "850": "Florida", "863": "Florida", "904": "Florida", "941": "Florida", "954": "Florida",
 "229": "Georgia", "404": "Georgia", "470": "Georgia", "478": "Georgia",
 "678": "Georgia", "706": "Georgia", "762": "Georgia", "770": "Georgia", "912": "Georgia",
 "808": "Hawaii", "208": "Idaho", "986": "Idaho",
 "217": "Illinois", "224": "Illinois", "309": "Illinois", "312": "Illinois",
 "331": "Illinois", "618": "Illinois", "630": "Illinois", "708": "Illinois",
 "773": "Illinois", "779": "Illinois", "815": "Illinois", "847": "Illinois", "872": "Illinois",
 "219": "Indiana", "260": "Indiana", "317": "Indiana", "463": "Indiana",
 "574": "Indiana", "765": "Indiana", "812": "Indiana", "930": "Indiana",
 "319": "Iowa", "515": "Iowa", "563": "Iowa", "641": "Iowa", "712": "Iowa",
 "316": "Kansas", "620": "Kansas", "785": "Kansas", "913": "Kansas",
 "270": "Kentucky", "364": "Kentucky", "502": "Kentucky", "606": "Kentucky", "859": "Kentucky",
 "225": "Louisiana", "318": "Louisiana", "337": "Louisiana", "504": "Louisiana", "985": "Louisiana",
 "207": "Maine",
 "240": "Maryland", "301": "Maryland", "410": "Maryland", "443": "Maryland", "667": "Maryland",
 "339": "Massachusetts", "351": "Massachusetts", "413": "Massachusetts", "508": "Massachusetts",
 "617": "Massachusetts", "774": "Massachusetts", "781": "Massachusetts",
 "857": "Massachusetts", "978": "Massachusetts",
 "231": "Michigan", "248": "Michigan", "269": "Michigan", "313": "Michigan",
 "517": "Michigan", "586": "Michigan", "616": "Michigan", "734": "Michigan",
 "810": "Michigan", "906": "Michigan", "947": "Michigan", "989": "Michigan",
 "218": "Minnesota", "320": "Minnesota", "507": "Minnesota",
 "612": "Minnesota", "651": "Minnesota", "763": "Minnesota", "952": "Minnesota",
 "228": "Mississippi", "601": "Mississippi", "662": "Mississippi", "769": "Mississippi",
 "314": "Missouri", "417": "Missouri", "573": "Missouri", "636": "Missouri",
 "660": "Missouri", "816": "Missouri",
 "406": "Montana", "308": "Nebraska", "402": "Nebraska", "531": "Nebraska",
 "702": "Nevada", "725": "Nevada", "775": "Nevada",
 "603": "New Hampshire",
 "201": "New Jersey", "551": "New Jersey", "609": "New Jersey", "640": "New Jersey",
 "732": "New Jersey", "848": "New Jersey", "856": "New Jersey", "862": "New Jersey",
 "908": "New Jersey", "973": "New Jersey",
 "505": "New Mexico", "575": "New Mexico",
 "212": "New York", "315": "New York", "332": "New York", "347": "New York",
 "516": "New York", "518": "New York", "585": "New York", "607": "New York",
 "631": "New York", "646": "New York", "680": "New York", "716": "New York",
 "718": "New York", "838": "New York", "845": "New York", "914": "New York",
 "917": "New York", "929": "New York", "934": "New York",
 "252": "North Carolina", "336": "North Carolina", "704": "North Carolina",
 "743": "North Carolina", "828": "North Carolina", "910": "North Carolina",
 "919": "North Carolina", "980": "North Carolina", "984": "North Carolina",
 "701": "North Dakota",
 "216": "Ohio", "220": "Ohio", "234": "Ohio", "330": "Ohio", "380": "Ohio",
 "419": "Ohio", "440": "Ohio", "513": "Ohio", "567": "Ohio", "614": "Ohio",
 "740": "Ohio", "937": "Ohio",
 "405": "Oklahoma", "539": "Oklahoma", "580": "Oklahoma", "918": "Oklahoma",
 "458": "Oregon", "503": "Oregon", "541": "Oregon", "971": "Oregon",
 "215": "Pennsylvania", "223": "Pennsylvania", "267": "Pennsylvania", "272": "Pennsylvania",
 "412": "Pennsylvania", "445": "Pennsylvania", "484": "Pennsylvania", "570": "Pennsylvania",
 "610": "Pennsylvania", "717": "Pennsylvania", "724": "Pennsylvania", "814": "Pennsylvania",
 "835": "Pennsylvania", "878": "Pennsylvania",
 "401": "Rhode Island",
 "803": "South Carolina", "839": "South Carolina", "843": "South Carolina",
 "854": "South Carolina", "864": "South Carolina",
 "605": "South Dakota",
 "423": "Tennessee", "615": "Tennessee", "629": "Tennessee", "731": "Tennessee",
 "865": "Tennessee", "901": "Tennessee", "931": "Tennessee",
 "210": "Texas", "214": "Texas", "254": "Texas", "281": "Texas", "325": "Texas",
 "346": "Texas", "361": "Texas", "409": "Texas", "430": "Texas", "432": "Texas",
 "469": "Texas", "512": "Texas", "682": "Texas", "713": "Texas", "726": "Texas",
 "737": "Texas", "806": "Texas", "817": "Texas", "830": "Texas", "832": "Texas",
 "903": "Texas", "915": "Texas", "936": "Texas", "940": "Texas", "956": "Texas",
 "972": "Texas", "979": "Texas",
 "385": "Utah", "435": "Utah", "801": "Utah", "802": "Vermont",
 "276": "Virginia", "434": "Virginia", "540": "Virginia", "571": "Virginia",
 "703": "Virginia", "757": "Virginia", "804": "Virginia",
 "206": "Washington", "253": "Washington", "360": "Washington", "425": "Washington",
 "509": "Washington", "564": "Washington", "202": "Washington DC",
 "304": "West Virginia", "681": "West Virginia",
 "262": "Wisconsin", "414": "Wisconsin", "534": "Wisconsin", "608": "Wisconsin",
 "715": "Wisconsin", "920": "Wisconsin", "307": "Wyoming",
 "787": "Puerto Rico", "939": "Puerto Rico", "340": "US Virgin Islands", "671": "Guam",
 "403": "Alberta", "587": "Alberta", "780": "Alberta", "825": "Alberta",
 "236": "British Columbia", "250": "British Columbia", "604": "British Columbia",
 "672": "British Columbia", "778": "British Columbia",
 "204": "Manitoba", "431": "Manitoba", "506": "New Brunswick", "709": "Newfoundland",
 "782": "Nova Scotia", "902": "Nova Scotia",
 "226": "Ontario", "249": "Ontario", "289": "Ontario", "343": "Ontario",
 "365": "Ontario", "416": "Ontario", "437": "Ontario", "519": "Ontario",
 "548": "Ontario", "613": "Ontario", "647": "Ontario", "705": "Ontario",
 "807": "Ontario", "905": "Ontario",
 "367": "Quebec", "418": "Quebec", "438": "Quebec", "450": "Quebec",
 "514": "Quebec", "579": "Quebec", "581": "Quebec", "819": "Quebec", "873": "Quebec",
 "306": "Saskatchewan", "639": "Saskatchewan",
}
US_STATE_TZ = {
 "Alabama": "America/Chicago", "Alaska": "America/Anchorage", "Arizona": "America/Phoenix",
 "Arkansas": "America/Chicago", "California": "America/Los_Angeles",
 "Colorado": "America/Denver", "Connecticut": "America/New_York", "Delaware": "America/New_York",
 "Florida": "America/New_York", "Georgia": "America/New_York", "Hawaii": "Pacific/Honolulu",
 "Idaho": "America/Boise", "Illinois": "America/Chicago",
 "Indiana": "America/Indiana/Indianapolis", "Iowa": "America/Chicago",
 "Kansas": "America/Chicago", "Kentucky": "America/New_York", "Louisiana": "America/Chicago",
 "Maine": "America/New_York", "Maryland": "America/New_York",
 "Massachusetts": "America/New_York", "Michigan": "America/Detroit",
 "Minnesota": "America/Chicago", "Mississippi": "America/Chicago",
 "Missouri": "America/Chicago", "Montana": "America/Denver", "Nebraska": "America/Chicago",
 "Nevada": "America/Los_Angeles", "New Hampshire": "America/New_York",
 "New Jersey": "America/New_York", "New Mexico": "America/Denver",
 "New York": "America/New_York", "North Carolina": "America/New_York",
 "North Dakota": "America/Chicago", "Ohio": "America/New_York",
 "Oklahoma": "America/Chicago", "Oregon": "America/Los_Angeles",
 "Pennsylvania": "America/New_York", "Rhode Island": "America/New_York",
 "South Carolina": "America/New_York", "South Dakota": "America/Chicago",
 "Tennessee": "America/Chicago", "Texas": "America/Chicago", "Utah": "America/Denver",
 "Vermont": "America/New_York", "Virginia": "America/New_York",
 "Washington": "America/Los_Angeles", "Washington DC": "America/New_York",
 "West Virginia": "America/New_York", "Wisconsin": "America/Chicago",
 "Wyoming": "America/Denver", "Puerto Rico": "America/Puerto_Rico",
 "Guam": "Pacific/Guam", "US Virgin Islands": "America/St_Thomas",
 "Alberta": "America/Edmonton", "British Columbia": "America/Vancouver",
 "Manitoba": "America/Winnipeg", "New Brunswick": "America/Halifax",
 "Newfoundland": "America/St_Johns", "Nova Scotia": "America/Halifax",
 "Ontario": "America/Toronto", "Quebec": "America/Toronto", "Saskatchewan": "America/Regina",
}

UK_CITY = {
 "020": "London", "023": "Southampton", "024": "Coventry", "028": "Belfast",
 "029": "Cardiff", "0113": "Leeds", "0114": "Sheffield", "0115": "Nottingham",
 "0116": "Leicester", "0117": "Bristol", "0118": "Reading", "0121": "Birmingham",
 "0131": "Edinburgh", "0141": "Glasgow", "0151": "Liverpool", "0161": "Manchester",
 "0191": "Newcastle", "01223": "Cambridge", "01224": "Aberdeen", "01273": "Brighton",
 "01642": "Middlesbrough", "01782": "Stoke-on-Trent", "01865": "Oxford",
 "01482": "Hull", "01902": "Wolverhampton", "01904": "York", "01382": "Dundee",
 "01228": "Carlisle", "01752": "Plymouth", "01202": "Bournemouth", "01923": "Watford",
}
INDIA_CITY = {
 "011": "Delhi", "022": "Mumbai", "033": "Kolkata", "044": "Chennai",
 "040": "Hyderabad", "080": "Bengaluru", "020": "Pune", "079": "Ahmedabad",
 "0141": "Jaipur", "0522": "Lucknow", "0755": "Bhopal", "0731": "Indore",
 "0751": "Gwalior", "0181": "Jalandhar", "0172": "Chandigarh", "0124": "Gurgaon",
 "0120": "Noida", "0484": "Kochi", "0471": "Thiruvananthapuram", "0422": "Coimbatore",
 "0431": "Tiruchirappalli", "0824": "Mangalore", "0832": "Goa", "0657": "Jamshedpur",
 "0612": "Patna", "0674": "Bhubaneswar", "0135": "Dehradun", "0240": "Aurangabad",
}


# Brazil DDD (2-digit area code) -> state
BR_DDD = {
 "11": "São Paulo", "12": "São Paulo", "13": "São Paulo", "14": "São Paulo",
 "15": "São Paulo", "16": "São Paulo", "17": "São Paulo", "18": "São Paulo",
 "19": "São Paulo", "21": "Rio de Janeiro", "22": "Rio de Janeiro", "24": "Rio de Janeiro",
 "27": "Espírito Santo", "28": "Espírito Santo",
 "31": "Minas Gerais", "32": "Minas Gerais", "33": "Minas Gerais", "34": "Minas Gerais",
 "35": "Minas Gerais", "37": "Minas Gerais", "38": "Minas Gerais",
 "41": "Paraná", "42": "Paraná", "43": "Paraná", "44": "Paraná", "45": "Paraná", "46": "Paraná",
 "47": "Santa Catarina", "48": "Santa Catarina", "49": "Santa Catarina",
 "51": "Rio Grande do Sul", "53": "Rio Grande do Sul", "54": "Rio Grande do Sul",
 "55": "Rio Grande do Sul", "61": "Distrito Federal", "62": "Goiás", "64": "Goiás",
 "63": "Tocantins", "65": "Mato Grosso", "66": "Mato Grosso", "67": "Mato Grosso do Sul",
 "68": "Acre", "69": "Rondônia", "71": "Bahia", "73": "Bahia", "74": "Bahia",
 "75": "Bahia", "77": "Bahia", "79": "Sergipe", "81": "Pernambuco", "87": "Pernambuco",
 "82": "Alagoas", "83": "Paraíba", "84": "Rio Grande do Norte", "85": "Ceará",
 "86": "Piauí", "88": "Ceará", "89": "Piauí", "91": "Pará", "92": "Amazonas",
 "93": "Pará", "94": "Pará", "95": "Roraima", "96": "Amapá", "97": "Amazonas",
 "98": "Maranhão", "99": "Maranhão",
}

# Mexico area codes -> state
MX_AREA = {
 "55": "Mexico City", "56": "Mexico City (metro)", "33": "Jalisco (Guadalajara)",
 "81": "Nuevo León (Monterrey)", "656": "Chihuahua (Cd. Juárez)",
 "664": "Baja California (Tijuana)", "686": "Baja California (Mexicali)",
 "662": "Sonora (Hermosillo)", "667": "Sinaloa (Culiacán)", "999": "Yucatán (Mérida)",
 "998": "Quintana Roo (Cancún)", "222": "Puebla", "449": "Aguascalientes",
 "777": "Morelos (Cuernavaca)", "442": "Querétaro", "871": "Coahuila (Torreón)",
 "844": "Coahuila (Saltillo)", "229": "Veracruz", "272": "Veracruz (Orizaba)",
 "312": "Colima", "314": "Colima (Manzanillo)", "612": "Baja California Sur (La Paz)",
 "443": "Michoacán (Morelia)", "477": "Guanajuato (León)", "461": "Guanajuato (Celaya)",
 "618": "Durango", "983": "Quintana Roo (Chetumal)", "993": "Tabasco (Villahermosa)",
 "444": "San Luis Potosí", "228": "Veracruz (Xalapa)", "961": "Chiapas (Tuxtla Gutiérrez)",
 "747": "Guerrero", "744": "Guerrero (Acapulco)", "722": "México (Toluca)",
}

# South Africa mobile prefix -> network, landline area -> city
ZA_MOBILE = {"060": "MTN", "061": "Rain", "062": "Cell C", "063": "Cell C",
 "064": "Cell C", "065": "Cell C", "066": "Telkom", "067": "Telkom", "068": "Telkom",
 "069": "Telkom", "071": "Vodacom", "072": "Vodacom", "073": "MTN", "074": "Cell C",
 "076": "Vodacom", "078": "MTN", "079": "Vodacom", "081": "Telkom", "082": "Vodacom",
 "083": "MTN", "084": "Cell C"}
ZA_AREA = {"010": "Johannesburg", "011": "Johannesburg", "012": "Pretoria",
 "013": "Mpumalanga", "014": "Rustenburg", "015": "Polokwane", "016": "Vaal Triangle",
 "017": "Ermelo", "018": "Potchefstroom", "021": "Cape Town", "022": "Western Cape",
 "023": "Worcester", "031": "Durban", "032": "KwaZulu-Natal", "033": "Pietermaritzburg",
 "034": "Newcastle", "035": "Richards Bay", "039": "Kokstad", "040": "East London",
 "041": "Gqeberha", "043": "Queenstown", "044": "George", "046": "Makhanda",
 "047": "Mthatha", "051": "Bloemfontein", "053": "Kimberley", "057": "Welkom"}

# Australia area code -> state
AU_AREA = {"2": "New South Wales / ACT", "3": "Victoria / Tasmania",
           "7": "Queensland", "8": "Western Australia / SA / NT"}

# Germany area code -> city (major)
DE_AREA = {"030": "Berlin", "040": "Hamburg", "089": "Munich", "069": "Frankfurt",
 "0711": "Stuttgart", "0221": "Cologne", "0211": "Düsseldorf", "0201": "Essen",
 "0231": "Dortmund", "0911": "Nuremberg", "0511": "Hanover", "0341": "Leipzig",
 "0351": "Dresden", "0621": "Mannheim", "0611": "Wiesbaden"}

# France zone -> region
FR_ZONES = {"01": "Île-de-France (Paris)", "02": "Northwest France",
            "03": "Northeast France", "04": "Southeast France", "05": "Southwest France"}

# Spain prefix -> region
ES_PREFIX = {"91": "Madrid", "93": "Barcelona", "94": "Basque Country (Bilbao)",
             "95": "Andalusia (Seville)", "96": "Valencia", "97": "Catalonia",
             "98": "Asturias / Galicia"}

# Italy area code -> city
IT_AREA = {"02": "Milan", "06": "Rome", "011": "Turin", "081": "Naples",
 "051": "Bologna", "055": "Florence", "041": "Venice", "010": "Genoa",
 "091": "Palermo", "095": "Catania", "070": "Cagliari", "080": "Bari",
 "045": "Verona", "049": "Padua"}


def _ke_network(p):
    p = p.lstrip("0")
    if p[:2] in ("70", "71", "72", "74", "79"):
        return "Safaricom"
    if p[:2] in ("73", "75"):
        return "Airtel"
    if p[:2] == "76":
        return "Equitel"
    if p[:2] == "77":
        return "Telkom"
    return None


def _eg_network(p):
    if p.startswith("10"):
        return "Vodafone"
    if p.startswith("11"):
        return "Etisalat"
    if p.startswith("12"):
        return "Orange"
    if p.startswith("15"):
        return "WE"
    return None


def _ru_network(p):
    if p[:3] in ("900", "901", "902", "903", "904", "905", "906", "908", "909",
                 "950", "951", "952", "953", "958"):
        return "Beeline"
    if p[:3] in ("910", "911", "912", "913", "914", "915", "916", "917", "918", "919",
                 "980", "981", "982", "983", "984", "985", "986", "987", "988", "989"):
        return "MTS"
    if p[:3] in ("921", "922", "923", "924", "925", "926", "927", "928", "929",
                 "930", "931", "932", "933", "934", "935", "936", "937", "938", "939",
                 "940", "941", "942", "943", "944", "945", "946", "947", "948", "949", "999"):
        return "MegaFon"
    if p[:3] in ("960", "961", "962", "963", "964", "965", "966", "967", "968", "969",
                 "995", "996", "997", "998"):
        return "Tele2"
    return None


def _tz_local_time(tzname):
    if not tzname:
        return None
    try:
        from datetime import datetime
        return datetime.now(ZoneInfo(tzname)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def lookup(raw, key_lookup=None, http_fetch=None):
    """Return structured phone-intel for `raw`. `key_lookup(name)` resolves API
    keys (e.g. NUMVERIFY_API_KEY); `http_fetch(url)` performs an HTTP GET and
    returns (status, raw_bytes, ctype) — both optional."""
    s = re.sub(r"[^\d+]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s:
        return {"error": "Enter a phone number, e.g. +2348012345678 or 0801 234 5678."}

    cc, national = None, ""
    if s.startswith("+"):
        digits = s[1:]
        for L in (3, 2, 1):
            if digits[:L] in COUNTRY_CODES:
                cc, national = digits[:L], digits[L:]
                break
        if not cc:
            return {"error": "Unknown country code (try +234 for Nigeria, +1 for US/Canada)."}
    else:
        digits = s
        if len(digits) == 11 and digits[0] == "0":
            cc, national = "234", digits[1:]       # Nigerian local format
        elif len(digits) == 10 and digits[0] in "23456789":
            cc, national = "1", digits             # NANP (US/Canada)
        else:
            for L in (3, 2, 1):
                if digits[:L] in COUNTRY_CODES:
                    cc, national = digits[:L], digits[L:]
                    break
            if not cc:
                cc, national = "1", digits         # fallback guess (NANP)

    national = national or ""
    e164 = "+" + cc + national
    valid = 4 <= len(national) <= 15
    cname, iso = COUNTRY_CODES.get(cc, ("Unknown", ""))
    out = {
        "input": raw, "e164": e164, "valid": valid,
        "country_code": cc, "country": cname, "iso": iso,
        "national": national, "digits": len(national),
    }

    region = carrier = line_type = None
    tz = COUNTRY_TZ.get(cc)

    if cc == "234":                                 # Nigeria
        n = national.lstrip("0")
        nat0 = "0" + n
        if nat0[:5] in NGN_MOBILE:
            carrier = NGN_MOBILE[nat0[:5]]
        elif nat0[:4] in NGN_MOBILE:
            carrier = NGN_MOBILE[nat0[:4]]
        if carrier:
            line_type, region = "mobile", "Nigeria"
        elif nat0[:3] in NGN_LANDLINE:
            region, line_type = NGN_LANDLINE[nat0[:3]] + ", Nigeria", "landline"
        elif nat0[:2] in NGN_LANDLINE:
            region, line_type = NGN_LANDLINE[nat0[:2]] + ", Nigeria", "landline"
        elif nat0[1:2] in ("7", "8", "9"):
            line_type = "mobile"
    elif cc == "1":                                 # NANP (US/Canada)
        area = national[:3]
        state = US_AREA.get(area)
        if state:
            region = state
            tz = US_STATE_TZ.get(state)
        else:
            region = "United States / Canada"
        line_type = "mobile/landline/VoIP (varies)"
    elif cc == "44":                                # UK
        n = national.lstrip("0")
        nat0 = "0" + n
        if n.startswith("7"):
            line_type = "mobile"
        elif n.startswith(("1", "2")):
            line_type = "landline"
        elif n.startswith("800"):
            line_type = "toll-free"
        for pref in sorted(UK_CITY, key=len, reverse=True):
            if nat0.startswith(pref):
                region = UK_CITY[pref] + ", UK"
                break
    elif cc == "91":                                # India
        n = national.lstrip("0")
        if n[:1] in "6789":
            line_type = "mobile"
        else:
            line_type = "landline"
            nat0 = "0" + n
            for pref in sorted(INDIA_CITY, key=len, reverse=True):
                if nat0.startswith(pref):
                    region = INDIA_CITY[pref] + ", India"
                    break
    elif cc == "233":                               # Ghana
        n = national.lstrip("0")
        if n[:1] in ("2", "5"):
            line_type = "mobile"
        elif n[:1] == "3":
            line_type = "landline"
        region = "Ghana"
    elif cc == "55":                                # Brazil
        area = national[:2]
        if area in BR_DDD:
            region = BR_DDD[area] + ", Brazil"
        if len(national) == 11 and national[2] == "9":
            line_type = "mobile"
        elif len(national) == 10:
            line_type = "landline"
    elif cc == "52":                                # Mexico
        n = national[1:] if national[:1] == "1" else national
        area3, area2 = n[:3], n[:2]
        if area3 in MX_AREA:
            region = MX_AREA[area3] + ", Mexico"
        elif area2 in MX_AREA:
            region = MX_AREA[area2] + ", Mexico"
        line_type = "mobile" if national[:1] == "1" else "landline/VoIP (likely)"
    elif cc == "27":                                # South Africa
        n = national.lstrip("0")
        nat0 = "0" + n
        if nat0[:3] in ZA_MOBILE:
            carrier = ZA_MOBILE[nat0[:3]]
            line_type = "mobile"
        elif nat0[:3] in ZA_AREA:
            region, line_type = ZA_AREA[nat0[:3]] + ", South Africa", "landline"
        elif nat0[:2] in ZA_AREA:
            region, line_type = ZA_AREA[nat0[:2]] + ", South Africa", "landline"
    elif cc == "254":                               # Kenya
        if national[:1] == "7":
            carrier = _ke_network(national)
            line_type = "mobile"
        elif national[:1] == "2":
            line_type = "landline"
            region = "Nairobi, Kenya" if national.startswith("20") else "Kenya"
    elif cc == "20":                                # Egypt
        if national[:1] == "1":
            carrier = _eg_network(national)
            line_type = "mobile"
        else:
            line_type = "landline"
    elif cc == "7":                                 # Russia / Kazakhstan
        if national[:1] == "9":
            carrier = _ru_network(national)
            line_type = "mobile"
        else:
            line_type = "landline"
    elif cc == "61":                                # Australia
        n = national.lstrip("0")
        if n[:1] == "4":
            line_type = "mobile"
        elif n[:1] in AU_AREA:
            region, line_type = AU_AREA[n[:1]] + ", Australia", "landline"
    elif cc == "49":                                # Germany
        n = national.lstrip("0")
        nat0 = "0" + n
        if nat0[:2] in ("15", "16", "17"):
            line_type = "mobile"
        else:
            line_type = "landline"
            for pref in sorted(DE_AREA, key=len, reverse=True):
                if nat0.startswith(pref):
                    region = DE_AREA[pref] + ", Germany"
                    break
    elif cc == "33":                                # France
        n = national.lstrip("0")
        if n[:1] in ("6", "7"):
            line_type = "mobile"
        else:
            line_type = "landline"
            nat0 = "0" + n
            if nat0[:2] in FR_ZONES:
                region = FR_ZONES[nat0[:2]] + ", France"
    elif cc == "34":                                # Spain
        n = national.lstrip("0")
        if n[:1] in ("6", "7"):
            line_type = "mobile"
        else:
            line_type = "landline"
            if n[:2] in ES_PREFIX:
                region = ES_PREFIX[n[:2]] + ", Spain"
    elif cc == "39":                                # Italy
        n = national.lstrip("0")
        if n[:1] == "3":
            line_type = "mobile"
        else:
            line_type = "landline"
            nat0 = "0" + n
            for pref in sorted(IT_AREA, key=len, reverse=True):
                if nat0.startswith(pref):
                    region = IT_AREA[pref] + ", Italy"
                    break

    if line_type is None and national and cc not in ("1",):
        if national[0] in "6789" and len(national) >= 9:
            line_type = "mobile (likely)"
        elif len(national) >= 8:
            line_type = "landline/VoIP (likely)"

    out["region"] = region
    out["carrier"] = carrier
    out["line_type"] = line_type
    out["timezone"] = tz
    out["local_time"] = _tz_local_time(tz)
    out["precision"] = ("state/region" if region else ("country" if cc else "unknown"))
    out["global"] = True

    # optional numverify enrichment (free key adds carrier/line-type verification)
    if key_lookup and http_fetch:
        nk = key_lookup("NUMVERIFY_API_KEY")
        if nk:
            try:
                url = ("https://apilayer.net/api/validate?access_key="
                       + urllib.parse.quote(nk) + "&number=" + urllib.parse.quote(e164)
                       + "&format=1")
                _, rawb, _ = http_fetch(url)
                nv = json.loads(rawb)
                if nv.get("valid") is not None:
                    out["numverify"] = {k: nv.get(k) for k in
                        ("valid", "number", "local_format", "international_format",
                         "country_code", "country_name", "location", "carrier", "line_type")}
            except Exception:
                out["numverify"] = {"error": "numverify lookup failed"}

    q = urllib.parse.quote(e164)
    q2 = urllib.parse.quote('"' + e164 + '"')
    out["search_links"] = [
        {"label": "Google (exact)", "url": "https://www.google.com/search?q=" + q2},
        {"label": "Google", "url": "https://www.google.com/search?q=" + q},
        {"label": "Bing", "url": "https://www.bing.com/search?q=" + q2},
        {"label": "DuckDuckGo", "url": "https://duckduckgo.com/?q=" + q2},
        {"label": "Yandex", "url": "https://yandex.com/search/?text=" + q2},
    ]
    out["note"] = ("Covers 200+ country codes globally. For supported countries this "
                   "resolves to state/region/city and carrier from the number's metadata. "
                   "Exact street-level or live-GPS location of a phone number is NOT possible "
                   "for civilians — only telecom operators/law enforcement can do that. "
                   "Phone numbers have no IP address: a phone's IP is private, dynamic and "
                   "only visible to its own network (or to a server it connects to), never to "
                   "a number lookup. The search links show where the number appears publicly.")
    return out
