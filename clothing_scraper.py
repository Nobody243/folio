#!/usr/bin/env python3
"""
clothing_scraper.py
===================

A single-file scraper that visits real clothing brand websites for a chosen
country and pulls public product listings. Brands that block the request are
silently skipped; the rest are displayed at the end.

Covered countries
-----------------
US, CA, GB, IE, AU, DE, NL, FR, IT, PK

Each country has 12–15 real clothing brands. Most are Shopify-based (which
exposes a public /products.json catalogue endpoint — the same one Google
Shopping and price comparison sites consume). Brands not on Shopify use a
JSON-LD HTML fallback that reads the same product data Google indexes.

How "skip on block" works
-------------------------
If a brand returns 401/403/429/503, times out, returns non-JSON, or breaks
on parse, the scraper raises ScraperBlocked, the orchestrator catches it,
and moves on. The brand is just listed under "skipped" at the end.

IMPORTANT — RUN THIS FROM A REGULAR HOME / LAPTOP IP
----------------------------------------------------
Cloudflare and similar CDNs blanket-block requests from datacenter IPs
(AWS, GCP, Azure, sandboxes, CI runners). If you run this from a cloud VM
you'll see almost everything skipped. Run from your laptop on a residential
connection for a real hit rate.

Dependencies
------------
    pip install requests

Usage
-----
    python clothing_scraper.py                                    # interactive
    python clothing_scraper.py --country US --query "black t-shirt"
    python clothing_scraper.py --country PK --query "kurta" --max 50
    python clothing_scraper.py --country GB --query "hoodie" --csv out.csv
    python clothing_scraper.py --country DE --query "" --json all_de.json
    python clothing_scraper.py --list-countries

    # Verify which brands actually accept your requests (recommended FIRST):
    python clothing_scraper.py --verify --country PK
    python clothing_scraper.py --verify --country US --json verify_us.json
    python clothing_scraper.py --verify-all --json verify_all.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import logging
import re
import threading
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import Optional

import requests

SUPPORTED_COUNTRIES = ["US", "CA", "GB", "IE", "AU", "DE", "NL", "FR", "IT", "PK"]


# ───────────────────────── BRAND REGISTRY ─────────────────────────────────────
# Format: (name, base_url, primary_country, [also_ships_to], currency, platform)
# platform: "shopify" → /products.json    |    "html" → JSON-LD HTML fallback

BRANDS: list[tuple[str, str, str, list[str], str, str]] = [

    # ═══ UNITED STATES (US) ═══
    ("Comfrt", "https://comfrt.com", "US", [], "USD", "shopify"),
    ("Apc Us", "https://www.apc-us.com", "US", [], "USD", "shopify"),
    ("Kisstherain", "https://kisstherain.us", "US", [], "USD", "shopify"),
    ("Shop", "https://shop.buyblack.org", "US", [], "USD", "shopify"),
    ("Wmnblack", "https://wmnblack.com", "US", [], "USD", "shopify"),
    ("Frontiers Man", "https://frontiers-man.com", "US", [], "USD", "shopify"),
    ("Champion", "https://www.champion.com", "US", [], "USD", "shopify"),
    ("Magiclinen", "https://magiclinen.com", "US", [], "USD", "shopify"),
    ("Carpasus", "https://carpasus.com", "US", [], "USD", "shopify"),
    ("Mecprimo", "https://mecprimo.com", "US", [], "USD", "shopify"),
    ("Blackloveboutique", "https://www.blackloveboutique.com", "US", [], "USD", "shopify"),
    ("Pitbullsports", "https://pitbullsports.com", "US", [], "USD", "shopify"),
    ("Brfashionsllc", "https://brfashionsllc.com", "US", [], "USD", "shopify"),
    ("Hellstar", "https://hellstar.com", "US", [], "USD", "shopify"),
    ("Unboundmerino", "https://unboundmerino.com", "US", [], "USD", "shopify"),
    ("Johnsonwoolenmills", "https://www.johnsonwoolenmills.com", "US", [], "USD", "shopify"),
    ("Shopsteelcity", "https://shopsteelcity.com", "US", [], "USD", "shopify"),
    ("Theclassictshirt", "https://theclassictshirt.com", "US", [], "USD", "shopify"),
    ("Epichoodie", "https://epichoodie.com", "US", [], "USD", "shopify"),
    ("Americanleatherco", "https://www.americanleatherco.com", "US", [], "USD", "shopify"),
    ("Redwhiteblueapparel", "https://redwhiteblueapparel.com", "US", [], "USD", "shopify"),
    ("Harvestandmill", "https://harvestandmill.com", "US", [], "USD", "shopify"),
    ("Whitefoxboutique", "https://whitefoxboutique.com", "US", [], "USD", "shopify"),
    ("Solidstate", "https://solidstate.clothing", "US", [], "USD", "shopify"),
    ("Goodwear", "https://www.goodwear.com", "US", [], "USD", "shopify"),
    ("Allamericanclothing", "https://www.allamericanclothing.com", "US", [], "USD", "shopify"),
    ("Boathouse", "https://www.boathouse.com", "US", [], "USD", "shopify"),
    ("Boldblackapparel", "https://boldblackapparel.com", "US", [], "USD", "shopify"),
    ("Colocool", "https://colocool.com", "US", [], "USD", "shopify"),
    ("Proudlyusa", "https://proudlyusa.com", "US", [], "USD", "shopify"),
    ("Blvck", "https://blvck.com", "US", [], "USD", "shopify"),
    ("Blackpridetees", "https://blackpridetees.com", "US", [], "USD", "shopify"),
    ("Theblackestco", "https://theblackestco.com", "US", [], "USD", "shopify"),
    ("Myblackclothing", "https://www.myblackclothing.com", "US", [], "USD", "shopify"),
    ("Melaninislife", "https://melaninislife.com", "US", [], "USD", "shopify"),
    ("Coldcultureworldwide", "https://coldcultureworldwide.com", "US", [], "USD", "shopify"),
    ("American Giant", "https://www.american-giant.com", "US", [], "USD", "shopify"),
    ("Dandyworldwide", "https://dandyworldwide.com", "US", [], "USD", "shopify"),
    ("Allbirds",            "https://www.allbirds.com",            "US", ["CA","GB","AU","DE"], "USD", "shopify"),
    ("Kith",                "https://kith.com",                    "US", ["CA","GB","JP"],      "USD", "shopify"),
    ("Taylor Stitch",       "https://www.taylorstitch.com",        "US", ["CA"],                "USD", "shopify"),
    ("Buck Mason",          "https://www.buckmason.com",           "US", ["CA","GB"],           "USD", "shopify"),
    ("Outdoor Voices",      "https://www.outdoorvoices.com",       "US", ["CA"],                "USD", "shopify"),
    ("Vuori",               "https://vuoriclothing.com",           "US", ["CA","GB","AU"],      "USD", "shopify"),
    ("Rhone",               "https://www.rhone.com",               "US", ["CA"],                "USD", "shopify"),
    ("Mack Weldon",         "https://mackweldon.com",              "US", ["CA"],                "USD", "shopify"),
    ("True Classic",        "https://trueclassic.com",             "US", ["CA","GB","AU","DE"], "USD", "shopify"),
    ("Tecovas",             "https://tecovas.com",                 "US", [],                    "USD", "shopify"),
    ("Knickerbocker",       "https://knickerbocker.nyc",           "US", ["CA","GB"],           "USD", "shopify"),
    ("Public Rec",          "https://publicrec.com",               "US", ["CA"],                "USD", "shopify"),
    ("Bombas",              "https://bombas.com",                  "US", ["CA"],                "USD", "shopify"),
    ("Marine Layer",        "https://www.marinelayer.com",         "US", ["CA"],                "USD", "shopify"),
    ("Faherty Brand",       "https://fahertybrand.com",            "US", ["CA"],                "USD", "shopify"),
    ("Outerknown",          "https://www.outerknown.com",          "US", ["CA","GB"],           "USD", "shopify"),
    ("Tradlands",           "https://tradlands.com",               "US", ["CA"],                "USD", "shopify"),
    ("Brixton",             "https://www.brixton.com",             "US", ["CA","GB","DE"],      "USD", "shopify"),
    ("Aviator Nation",      "https://aviatornation.com",           "US", ["CA","GB","AU"],      "USD", "shopify"),
    ("State Bicycle Co",    "https://www.statebicycle.com",        "US", ["CA"],                "USD", "shopify"),
    ("Howler Brothers",     "https://www.howlerbros.com",          "US", ["CA"],                "USD", "shopify"),
    ("Flint and Tinder",    "https://huckberry.com/store/flint-and-tinder","US",["CA"],         "USD", "shopify"),
    ("Bonobos",             "https://bonobos.com",                 "US", ["CA"],                "USD", "shopify"),
    ("UNTUCKit",            "https://www.untuckit.com",            "US", ["CA","GB"],           "USD", "shopify"),
    ("Stio",                "https://www.stio.com",                "US", ["CA"],                "USD", "shopify"),

    # ═══ CANADA (CA) ═══
    ("Frankandoak", "https://www.frankandoak.com", "CA", [], "CAD", "shopify"),
    ("T Shirtbear", "https://t-shirtbear.com", "CA", [], "CAD", "html"),
    ("Jdsports", "https://jdsports.ca", "CA", [], "CAD", "shopify"),
    ("Juicycouture", "https://juicycouture.com", "CA", [], "CAD", "shopify"),
    ("Onlinewarehousesale", "https://onlinewarehousesale.com", "CA", [], "CAD", "shopify"),
    ("Ohcanadashop", "https://www.ohcanadashop.com", "CA", [], "CAD", "shopify"),
    ("Aelfriceden", "https://www.aelfriceden.com", "CA", [], "CAD", "shopify"),
    ("Fleecefactory", "https://fleecefactory.com", "CA", [], "CAD", "shopify"),
    ("Fairweatherclothing", "https://www.fairweatherclothing.com", "CA", [], "CAD", "shopify"),
    ("Canadaweathergear", "https://canadaweathergear.com", "CA", [], "CAD", "shopify"),
    ("Quartz Co", "https://quartz-co.com", "CA", [], "CAD", "shopify"),
    ("Woodpeckercanada", "https://woodpeckercanada.com", "CA", [], "CAD", "shopify"),
    ("Mooseknucklescanada", "https://www.mooseknucklescanada.com", "CA", [], "CAD", "shopify"),
    ("Getplenty", "https://www.getplenty.com", "CA", [], "CAD", "shopify"),
    ("Boathousestores", "https://boathousestores.com", "CA", [], "CAD", "shopify"),
    ("Psychobunny", "https://www.psychobunny.com", "CA", [], "CAD", "shopify"),
    ("Hoodiescanada", "https://hoodiescanada.ca", "CA", [], "CAD", "shopify"),
    ("Resistclothing", "https://resistclothing.ca", "CA", [], "CAD", "shopify"),
    ("Plb Store", "https://plb-store.com", "CA", [], "CAD", "shopify"),
    ("Choosecanadian", "https://choosecanadian.ca", "CA", [], "CAD", "shopify"),
    ("Tentree",             "https://www.tentree.com",             "CA", ["US","GB","DE"],      "CAD", "shopify"),
    ("Frank And Oak",       "https://frankandoak.com",             "CA", ["US"],                "CAD", "shopify"),
    ("Kotn",                "https://kotn.com",                    "CA", ["US","GB"],           "CAD", "shopify"),
    ("Peace Collective",    "https://peace-collective.com",        "CA", ["US"],                "CAD", "shopify"),
    ("Province of Canada",  "https://provinceofcanada.com",        "CA", ["US"],                "CAD", "shopify"),
    ("Encircled",           "https://encircled.ca",                "CA", ["US"],                "CAD", "shopify"),
    ("Wuxly Movement",      "https://wuxly.com",                   "CA", ["US"],                "CAD", "shopify"),
    ("Muttonhead",          "https://muttonheadstore.com",         "CA", ["US"],                "CAD", "shopify"),
    ("Reigning Champ",      "https://reigningchamp.com",           "CA", ["US"],                "CAD", "shopify"),
    ("Brunette the Label",  "https://brunettethelabel.com",        "CA", ["US"],                "CAD", "shopify"),
    ("Redwood Classics",    "https://redwoodclassics.net",         "CA", ["US"],                "CAD", "shopify"),
    ("Naked & Famous Denim","https://www.nakedandfamousdenim.com", "CA", ["US"],                "CAD", "shopify"),
    ("Ten Tree Apparel",    "https://www.tentree.ca",              "CA", ["US"],                "CAD", "shopify"),
    ("Smash + Tess",        "https://smashtess.com",               "CA", ["US"],                "CAD", "shopify"),
    ("Hilary MacMillan",    "https://hilarymacmillan.com",         "CA", ["US"],                "CAD", "shopify"),
    ("Pillar Outdoor",      "https://www.pillaroutdoor.com",       "CA", ["US"],                "CAD", "shopify"),
    ("Coalatree",           "https://coalatree.com",               "CA", ["US"],                "CAD", "shopify"),
    ("Roots Canada",        "https://www.roots.com",               "CA", ["US"],                "CAD", "html"),
    ("Saxx Underwear",      "https://ca.saxxunderwear.com",        "CA", ["US"],                "CAD", "shopify"),
    ("Triarchy",            "https://triarchy.com",                "CA", ["US"],                "CAD", "shopify"),
    ("Bleusalt",            "https://bleusalt.com",                "CA", ["US"],                "CAD", "shopify"),
    ("Free Label",          "https://freelabel.ca",                "CA", ["US"],                "CAD", "shopify"),
    ("Anian",               "https://www.anianmfg.com",            "CA", ["US"],                "CAD", "shopify"),
    ("Outerknown CA",       "https://ca.outerknown.com",           "CA", ["US"],                "CAD", "shopify"),
    ("Article 22",          "https://article22.ca",                "CA", ["US"],                "CAD", "shopify"),

    # ═══ UNITED KINGDOM (GB) ═══
    ("Boy London", "https://www.boy-london.com", "GB", [], "GBP", "shopify"),
    ("Thomaspink", "https://thomaspink.com", "GB", [], "GBP", "shopify"),
    ("Representclo", "https://representclo.com", "GB", [], "GBP", "shopify"),
    ("Lyleandscott", "https://www.lyleandscott.com", "GB", [], "GBP", "shopify"),
    ("Thecottonlondon", "https://www.thecottonlondon.com", "GB", [], "GBP", "shopify"),
    ("Myneedsaresimple", "https://www.myneedsaresimple.co.uk", "GB", [], "GBP", "shopify"),
    ("Spoiledbrat", "https://spoiledbrat.co.uk", "GB", [], "GBP", "shopify"),
    ("Blakelyclothing", "https://blakelyclothing.com", "GB", [], "GBP", "shopify"),
    ("Uk", "https://uk.representclo.com", "GB", [], "GBP", "shopify"),
    ("Championstore", "https://www.championstore.com", "GB", [], "GBP", "shopify"),
    ("Morningclubclothing", "https://www.morningclubclothing.co.uk", "GB", [], "GBP", "shopify"),
    ("Dreambutdonotsleep", "https://dreambutdonotsleep.com", "GB", [], "GBP", "shopify"),
    ("Threadheads", "https://threadheads.com", "GB", [], "GBP", "shopify"),
    ("Lavenderhillclothing", "https://www.lavenderhillclothing.com", "GB", [], "GBP", "shopify"),
    ("Shopcaterpillar", "https://www.shopcaterpillar.co.uk", "GB", [], "GBP", "shopify"),
    ("Eu", "https://eu.icebreaker.com", "GB", [], "GBP", "shopify"),
    ("Lanxshoes", "https://lanxshoes.com", "GB", [], "GBP", "shopify"),
    ("Theprettydresscompany", "https://www.theprettydresscompany.com", "GB", [], "GBP", "shopify"),
    ("Napapijri", "https://www.napapijri.com", "GB", [], "GBP", "shopify"),
    ("Jstoremart", "https://jstoremart.com", "GB", [], "GBP", "shopify"),
    ("Tedbaker", "https://www.tedbaker.com", "GB", [], "GBP", "shopify"),
    ("Gymshark",            "https://www.gymshark.com",            "GB", ["US","DE","AU","IE"], "GBP", "shopify"),
    ("Lucy & Yak",          "https://lucyandyak.com",              "GB", ["IE","DE","FR","NL"], "GBP", "shopify"),
    ("Percival",            "https://www.percivalclo.com",         "GB", ["IE","DE","FR","NL"], "GBP", "shopify"),
    ("Finisterre",          "https://finisterre.com",              "GB", ["IE","DE","FR","NL"], "GBP", "shopify"),
    ("Manors Golf",         "https://manorsgolf.com",              "GB", ["US"],                "GBP", "shopify"),
    ("Drake\'s London",     "https://drakes.com",                  "GB", ["US","IE"],           "GBP", "shopify"),
    ("Rapha",               "https://www.rapha.cc",                "GB", ["IE","DE","FR","US"], "GBP", "shopify"),
    ("Universal Works",     "https://universalworks.co.uk",        "GB", ["IE","DE","FR"],      "GBP", "shopify"),
    ("YMC London",          "https://www.youmustcreate.com",       "GB", ["IE","DE","FR"],      "GBP", "shopify"),
    ("Albam Clothing",      "https://albamclothing.com",           "GB", ["IE"],                "GBP", "shopify"),
    ("Wax London",          "https://waxlondon.com",               "GB", ["IE","DE"],           "GBP", "shopify"),
    ("Sunspel",             "https://www.sunspel.com",             "GB", ["IE","US","DE"],      "GBP", "shopify"),
    ("Birdsong",            "https://birdsong.london",             "GB", ["IE"],                "GBP", "shopify"),
    ("Community Clothing",  "https://communityclothing.co.uk",     "GB", ["IE","DE","FR"],      "GBP", "shopify"),
    ("Tom Cridland",        "https://tomcridland.com",             "GB", ["IE","US","DE"],      "GBP", "shopify"),
    ("Lavenham",            "https://www.lavenhamjackets.com",     "GB", ["IE","DE","FR"],      "GBP", "shopify"),
    ("Folk Clothing",       "https://www.folkclothing.com",        "GB", ["IE","DE","FR"],      "GBP", "shopify"),
    ("Nudie Jeans UK",      "https://www.nudiejeans.com",          "GB", ["IE","DE","FR","NL"], "GBP", "shopify"),
    ("Komodo",              "https://komodo.co.uk",                "GB", ["IE","DE"],           "GBP", "shopify"),
    ("Thought Clothing",    "https://www.thought.com",             "GB", ["IE","DE","FR"],      "GBP", "shopify"),
    ("Beaumont Organic",    "https://www.beaumontorganic.com",     "GB", ["IE","DE","FR"],      "GBP", "shopify"),
    ("Riley Studio",        "https://riley-studio.com",            "GB", ["IE","DE","FR"],      "GBP", "shopify"),
    ("Joseph Turner",       "https://www.josephturner.co.uk",      "GB", ["IE"],                "GBP", "shopify"),
    ("Howies",              "https://www.howies.co.uk",            "GB", ["IE","DE"],           "GBP", "shopify"),
    ("Rapanui",             "https://rapanuiclothing.com",         "GB", ["IE","DE"],           "GBP", "shopify"),

    # ═══ IRELAND (IE) ═══
    ("Huhclothing", "https://huhclothing.com", "IE", [], "EUR", "shopify"),
    ("Thombrowne", "https://www.thombrowne.com", "IE", [], "EUR", "shopify"),
    ("Mcelhinneys", "https://www.mcelhinneys.com", "IE", [], "EUR", "shopify"),
    ("Dropdead", "https://dropdead.world", "IE", [], "EUR", "shopify"),
    ("Elverys", "https://www.elverys.ie", "IE", [], "EUR", "shopify"),
    ("Shop", "https://shop.thetemplebarpub.com", "IE", [], "EUR", "shopify"),
    ("Replaymenswear", "https://replaymenswear.ie", "IE", [], "EUR", "shopify"),
    ("Theirishstore", "https://www.theirishstore.com", "IE", [], "EUR", "shopify"),
    ("80Scasualclassics", "https://www.80scasualclassics.co.uk", "IE", [], "EUR", "shopify"),
    ("Indigoandcloth", "https://indigoandcloth.com", "IE", [], "EUR", "shopify"),
    ("Paulsmenswear", "https://www.paulsmenswear.ie", "IE", [], "EUR", "shopify"),
    ("Therapyboutique", "https://therapyboutique.ie", "IE", [], "EUR", "shopify"),
    ("Carrollsirishgifts", "https://www.carrollsirishgifts.com", "IE", [], "EUR", "shopify"),
    ("Weaversofireland", "https://weaversofireland.com", "IE", [], "EUR", "shopify"),
    ("Spiritclothing", "https://spiritclothing.ie", "IE", [], "EUR", "shopify"),
    ("Dervansfashions", "https://dervansfashions.ie", "IE", [], "EUR", "shopify"),
    ("Tonn", "https://tonn.shop", "IE", [], "EUR", "shopify"),
    ("Iclothing", "https://www.iclothing.com", "IE", [], "EUR", "shopify"),
    ("Magee 1866",          "https://magee1866.com",               "IE", ["GB","US"],           "EUR", "shopify"),
    ("Aran Sweater Market", "https://www.aransweatermarket.com",   "IE", ["GB","US","DE","FR"], "EUR", "shopify"),
    ("Aran Crafts",         "https://www.aran.com",                "IE", ["GB","US"],           "EUR", "shopify"),
    ("Carraig Donn",        "https://www.carraigdonn.com",         "IE", ["GB"],                "EUR", "shopify"),
    ("Inis Meáin",          "https://www.inismeain.ie",            "IE", ["GB","US","DE"],      "EUR", "shopify"),
    ("McNutt of Donegal",   "https://mcnuttofdonegal.com",         "IE", ["GB","US"],           "EUR", "shopify"),
    ("Foxford",             "https://foxford.com",                 "IE", ["GB","US","DE"],      "EUR", "shopify"),
    ("Triona Design",       "https://trionadesign.com",            "IE", ["GB","US"],           "EUR", "shopify"),
    ("Lennon Courtney",     "https://lennoncourtney.com",          "IE", ["GB"],                "EUR", "shopify"),
    ("Hanna Hats",          "https://www.hannahats.com",           "IE", ["GB","US","DE"],      "EUR", "shopify"),
    ("Heart of Ireland",    "https://www.heartofireland.com",      "IE", ["GB","US"],           "EUR", "shopify"),
    ("Cleo Ltd",            "https://www.cleo-ltd.com",            "IE", ["GB"],                "EUR", "shopify"),
    ("Aran Woollen Mills",  "https://aranwoollenmills.com",        "IE", ["GB","US","DE"],      "EUR", "shopify"),
    ("Donegal Tweed",       "https://www.donegalshop.com",         "IE", ["GB","US"],           "EUR", "shopify"),
    ("Fisherman Out of Ireland","https://fishermanoutofireland.com","IE",["GB","US"],           "EUR", "shopify"),
    ("Gaelsong",            "https://www.gaelsong.com",            "IE", ["GB","US"],           "EUR", "shopify"),
    ("West Cork Knitwear",  "https://westcorkknitwear.com",        "IE", ["GB","US"],           "EUR", "shopify"),
    ("Studio Donegal",      "https://www.studiodonegal.ie",        "IE", ["GB","US"],           "EUR", "shopify"),
    ("Spurr Boutique",      "https://www.spurr.ie",                "IE", ["GB"],                "EUR", "shopify"),
    ("Folkster",            "https://folkster.com",                "IE", ["GB"],                "EUR", "shopify"),
    ("Manning Cartell IE",  "https://www.manningcartell.com",      "IE", ["GB","AU"],           "EUR", "shopify"),
    ("Pamela Scott",        "https://www.pamelascott.com",         "IE", ["GB"],                "EUR", "shopify"),
    ("Ballantynes",         "https://www.ballantynes.com",         "IE", ["GB"],                "EUR", "shopify"),
    ("Caoimhe",             "https://caoimhejewellery.com",        "IE", ["GB","US"],           "EUR", "shopify"),
    ("The Tweed Project",   "https://www.thetweedproject.com",     "IE", ["GB","US"],           "EUR", "shopify"),

    # ═══ AUSTRALIA (AU) ═══
    ("Roxyaustralia", "https://www.roxyaustralia.com.au", "AU", [], "AUD", "shopify"),
    ("Champion", "https://www.champion.com.au", "AU", [], "AUD", "shopify"),
    ("Youknowclothing", "https://youknowclothing.com", "AU", [], "AUD", "shopify"),
    ("Tragicbeautiful", "https://www.tragicbeautiful.com", "AU", [], "AUD", "shopify"),
    ("Gluestore", "https://www.gluestore.com.au", "AU", [], "AUD", "shopify"),
    ("Vinniesonline", "https://vinniesonline.com.au", "AU", [], "AUD", "shopify"),
    ("Culturekings", "https://www.culturekings.com.au", "AU", [], "AUD", "shopify"),
    ("Gazman", "https://www.gazman.com.au", "AU", [], "AUD", "shopify"),
    ("Au", "https://au.globebrand.com", "AU", [], "AUD", "shopify"),
    ("Eyesonfloyd", "https://eyesonfloyd.com", "AU", [], "AUD", "shopify"),
    ("Rodneyclark", "https://www.rodneyclark.com", "AU", [], "AUD", "shopify"),
    ("Floandfrankie", "https://floandfrankie.com.au", "AU", [], "AUD", "shopify"),
    ("Thehoodiestore", "https://thehoodiestore.com.au", "AU", [], "AUD", "shopify"),
    ("Blueillusion", "https://blueillusion.com", "AU", [], "AUD", "shopify"),
    ("Colettehayman", "https://www.colettehayman.com.au", "AU", [], "AUD", "shopify"),
    ("Ameisefashion", "https://www.ameisefashion.com.au", "AU", [], "AUD", "shopify"),
    ("Camarguefashion", "https://camarguefashion.com.au", "AU", [], "AUD", "shopify"),
    ("Parfoispuertorico", "https://parfoispuertorico.com", "AU", [], "AUD", "shopify"),
    ("Cadelleleather", "https://cadelleleather.com.au", "AU", [], "AUD", "shopify"),
    ("Discountshoponline", "https://discountshoponline.com.au", "AU", [], "AUD", "shopify"),
    ("Blackcoltclothing", "https://blackcoltclothing.com", "AU", [], "AUD", "shopify"),
    ("Generalpants", "https://www.generalpants.com", "AU", [], "AUD", "shopify"),
    ("Universalstore", "https://www.universalstore.com", "AU", [], "AUD", "shopify"),
    ("Goondiwindicotton", "https://goondiwindicotton.com.au", "AU", [], "AUD", "shopify"),
    ("Princesshighway", "https://princesshighway.com.au", "AU", [], "AUD", "shopify"),
    ("Naturessocksaustralia", "https://www.naturessocksaustralia.com.au", "AU", [], "AUD", "shopify"),
    ("Sockdaily", "https://sockdaily.com", "AU", [], "AUD", "shopify"),
    ("Indifeels", "https://indifeels.com", "AU", [], "AUD", "shopify"),
    ("Academybrand", "https://academybrand.com", "AU", [], "AUD", "shopify"),
    ("Princess Polly",      "https://www.princesspolly.com",       "AU", ["US","GB","CA","NZ"], "USD", "shopify"),
    ("Showpo",              "https://www.showpo.com",              "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Beginning Boutique",  "https://www.beginningboutique.com",   "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Sabo",                "https://saboskirt.com",               "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Aje",                 "https://www.aje.com.au",              "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("P.E Nation",          "https://www.pe-nation.com",           "AU", ["US","GB"],           "AUD", "shopify"),
    ("Bassike",             "https://www.bassike.com",             "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Camilla and Marc",    "https://camillaandmarc.com",          "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Spell",               "https://www.spell.co",                "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Auguste the Label",   "https://www.augustethelabel.com",     "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Faithfull the Brand", "https://www.faithfullthebrand.com",   "AU", ["US","GB"],           "AUD", "shopify"),
    ("Kookai",              "https://www.kookai.com.au",           "AU", ["NZ"],                "AUD", "shopify"),
    ("Zulu & Zephyr",       "https://zuluandzephyr.com",           "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Sir the Label",       "https://www.sirthelabel.com",         "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Ksubi",               "https://ksubi.com",                   "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Country Road",        "https://www.countryroad.com.au",      "AU", ["NZ"],                "AUD", "html"),
    ("Bonds",               "https://www.bonds.com.au",            "AU", ["NZ"],                "AUD", "shopify"),
    ("Cotton On",           "https://cottonon.com",                "AU", ["NZ","US","GB"],      "AUD", "html"),
    ("White Fox Boutique",  "https://www.whitefoxboutique.com",    "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Aleph Eyewear",       "https://www.aleph-eyewear.com",       "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Ena Pelly",           "https://enapelly.com",                "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Bec + Bridge",        "https://www.becandbridge.com.au",     "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Sass & Bide",         "https://www.sassandbide.com",         "AU", ["US","GB","NZ"],      "AUD", "shopify"),
    ("Mavi Australia",      "https://www.mavi.com.au",             "AU", ["NZ"],                "AUD", "shopify"),
    ("Kivari",              "https://kivari.com.au",               "AU", ["US","GB","NZ"],      "AUD", "shopify"),

    # ═══ GERMANY (DE) ═══
    ("Sidemenclothing", "https://sidemenclothing.com", "DE", [], "EUR", "shopify"),
    ("Gate194", "https://gate194.berlin", "DE", [], "EUR", "shopify"),
    ("Eu", "https://eu.blakelyclothing.com", "DE", [], "EUR", "shopify"),
    ("Merzbschwanen", "https://www.merzbschwanen.com", "DE", [], "EUR", "shopify"),
    ("Derschutze", "https://derschutze.com", "DE", [], "EUR", "shopify"),
    ("Lovebrand", "https://lovebrand.com", "DE", [], "EUR", "shopify"),
    ("Eigensinnig Wien", "https://eigensinnig-wien.com", "DE", [], "EUR", "shopify"),
    ("Old Money", "https://old-money.com", "DE", [], "EUR", "shopify"),
    ("Syedvintage", "https://syedvintage.co.uk", "DE", [], "EUR", "shopify"),
    ("Eu", "https://eu.brandymelville.com", "DE", [], "EUR", "shopify"),
    ("Eu", "https://eu.peserico.com", "DE", [], "EUR", "shopify"),
    ("Eu", "https://eu.suitnegozi.com", "DE", [], "EUR", "shopify"),
    ("Germanoutfits", "https://germanoutfits.com", "DE", [], "EUR", "shopify"),
    ("Roots Germany", "https://roots-germany.de", "DE", [], "EUR", "shopify"),
    ("Aintforever", "https://aintforever.com", "DE", [], "EUR", "shopify"),
    ("ARMEDANGELS",         "https://www.armedangels.com",         "DE", ["NL","FR","IT","GB"], "EUR", "shopify"),
    ("Wunderwerk",          "https://wunderwerk.com",              "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Lanius",              "https://www.lanius.com",              "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Recolution",          "https://recolution.de",               "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Phyne",               "https://www.phyne.com",               "DE", ["NL","FR","IT"],     "EUR", "shopify"),
    ("Erlich Textil",       "https://www.erlich-textil.de",        "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Hessnatur",           "https://www.hessnatur.com",           "DE", ["NL","FR","IT"],     "EUR", "shopify"),
    ("Glore",               "https://www.glore.de",                "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Living Crafts",       "https://www.livingcrafts.de",         "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Loveco",              "https://loveco.de",                   "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Bleed Clothing",      "https://www.bleed-clothing.com",      "DE", ["NL","FR","IT"],     "EUR", "shopify"),
    ("Greenality",          "https://www.greenality.de",           "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Dedicated Brand",     "https://www.dedicatedbrand.com",      "DE", ["NL","FR","GB","SE"],"EUR", "shopify"),
    ("Bruno Banani",        "https://www.brunobanani.com",         "DE", ["NL","AT"],           "EUR", "shopify"),
    ("Aevor",               "https://aevor.com",                   "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Pinqponq",            "https://www.pinqponq.com",            "DE", ["NL","FR","GB"],     "EUR", "shopify"),
    ("Closed Hamburg",      "https://www.closed.com",              "DE", ["NL","FR","IT"],     "EUR", "shopify"),
    ("MUD Jeans DE",        "https://mudjeans.de",                 "DE", ["NL"],                "EUR", "shopify"),
    ("Knowledge Cotton DE", "https://www.knowledgecottonapparel.com","DE",["NL","FR","DK"],   "EUR", "shopify"),
    ("Embassy of Bricks",   "https://www.embassy-of-bricks-and-logs.de","DE",["NL","FR"],     "EUR", "shopify"),
    ("Ecoalf DE",           "https://ecoalf.com",                  "DE", ["ES","FR","IT"],     "EUR", "shopify"),
    ("People Tree DE",      "https://www.peopletree.co.uk",        "DE", ["GB","NL","FR"],     "EUR", "shopify"),
    ("Reer3",               "https://www.reer3.com",               "DE", ["NL","FR"],           "EUR", "shopify"),
    ("Format Klassisch",    "https://www.format-klassisch.com",    "DE", ["NL","AT"],           "EUR", "shopify"),
    ("Spath Designs",       "https://www.spaeth-designs.de",       "DE", ["AT"],                "EUR", "shopify"),

    # ═══ NETHERLANDS (NL) ═══
    ("Kykuclothing", "https://www.kykuclothing.com", "NL", [], "EUR", "shopify"),
    ("Droledemonsieur", "https://droledemonsieur.com", "NL", [], "EUR", "shopify"),
    ("Thrifttale", "https://thrifttale.com", "NL", [], "EUR", "shopify"),
    ("Collectthelabel", "https://collectthelabel.com", "NL", [], "EUR", "shopify"),
    ("Shop", "https://shop.swedenunlimited.com", "NL", [], "EUR", "shopify"),
    ("Wearnepra", "https://www.wearnepra.com", "NL", [], "EUR", "shopify"),
    ("Hinoya", "https://hinoya.shop", "NL", [], "EUR", "shopify"),
    ("Versegoodstore", "https://versegoodstore.com", "NL", [], "EUR", "shopify"),
    ("Trunkclothiers", "https://www.trunkclothiers.com", "NL", [], "EUR", "shopify"),
    ("Scandinaviannorth", "https://www.scandinaviannorth.com", "NL", [], "EUR", "shopify"),
    ("Icon Amsterdam", "https://icon-amsterdam.com", "NL", [], "EUR", "shopify"),
    ("Darntough", "https://darntough.com", "NL", [], "EUR", "shopify"),
    ("Labelsfashion", "https://labelsfashion.com", "NL", [], "EUR", "shopify"),
    ("Bagrustore", "https://www.bagrustore.com", "NL", [], "EUR", "shopify"),
    ("Shoplibas", "https://www.shoplibas.com", "NL", [], "EUR", "shopify"),
    ("Phoebephilo", "https://www.phoebephilo.com", "NL", [], "EUR", "shopify"),
    ("Panaprium", "https://www.panaprium.com", "NL", [], "EUR", "shopify"),
    ("Fouramsterdam", "https://www.fouramsterdam.com", "NL", [], "EUR", "shopify"),
    ("Colourful Rebel",     "https://colourfulrebel.com",          "NL", ["DE","BE","FR","GB"], "EUR", "shopify"),
    ("Patta",               "https://patta.nl",                    "NL", ["DE","BE","FR","GB"], "EUR", "shopify"),
    ("Daily Paper",         "https://www.dailypaperclothing.com",  "NL", ["DE","BE","FR","GB","US"],"EUR","shopify"),
    ("Studio Anneloes",     "https://www.studio-anneloes.com",     "NL", ["DE","BE","FR"],     "EUR", "shopify"),
    ("Mud Jeans",           "https://mudjeans.eu",                 "NL", ["DE","BE","FR","GB"],"EUR", "shopify"),
    ("Kings of Indigo",     "https://www.kingsofindigo.com",       "NL", ["DE","BE","FR","GB"],"EUR", "shopify"),
    ("Nukus",               "https://www.nukus.nl",                "NL", ["DE","BE"],          "EUR", "shopify"),
    ("MOOST Wanted",        "https://www.mooswanted.com",          "NL", ["DE","BE"],          "EUR", "shopify"),
    ("Just Female",         "https://www.justfemale.com",          "NL", ["DE","BE","FR","GB"],"EUR", "shopify"),
    ("HNST Jeans",          "https://hnstjeans.com",               "NL", ["DE","BE"],          "EUR", "shopify"),
    ("Suit Up",             "https://suitup.nl",                   "NL", ["BE","DE"],          "EUR", "shopify"),
    ("Paper Label",         "https://www.paperlabel.com",          "NL", ["DE","BE","FR"],     "EUR", "shopify"),
    ("Ese O Ese",           "https://www.eseoese.com",             "NL", ["DE","BE","ES"],     "EUR", "shopify"),
    ("Olaf Hussein",        "https://olafhussein.com",             "NL", ["DE","BE","FR","GB"],"EUR", "shopify"),
    ("Filling Pieces",      "https://www.fillingpieces.com",       "NL", ["DE","BE","FR","GB"],"EUR", "shopify"),
    ("Selected Femme NL",   "https://www.selected.com",            "NL", ["DE","BE","FR"],     "EUR", "shopify"),
    ("MS Mode",             "https://www.msmode.nl",               "NL", ["BE"],                "EUR", "shopify"),
    ("Yaya the Brand",      "https://www.yaya.com",                "NL", ["DE","BE","FR"],     "EUR", "shopify"),
    ("Maium Amsterdam",     "https://www.maium.com",               "NL", ["DE","BE","FR"],     "EUR", "shopify"),
    ("Pomandere NL",        "https://www.pomandere.com",           "NL", ["DE","IT","FR"],     "EUR", "shopify"),
    ("Catwalk Junkie",      "https://www.catwalkjunkie.com",       "NL", ["DE","BE","FR"],     "EUR", "shopify"),
    ("Geisha Fashion",      "https://www.geishafashion.com",       "NL", ["DE","BE"],          "EUR", "shopify"),
    ("Ydence",              "https://www.ydence.com",              "NL", ["DE","BE"],          "EUR", "shopify"),
    ("Hema",                "https://www.hema.nl",                 "NL", ["DE","BE","FR"],     "EUR", "html"),
    ("Vanilia",             "https://www.vanilia.com",             "NL", ["DE","BE"],          "EUR", "shopify"),

    # ═══ FRANCE (FR) ═══
    ("Walkinparis", "https://walkinparis.com", "FR", [], "EUR", "shopify"),
    ("Alive Sr", "https://www.alive-sr.co.uk", "FR", [], "EUR", "shopify"),
    ("Ateliertb", "https://ateliertb.com", "FR", [], "EUR", "shopify"),
    ("Findthegoodbrand", "https://findthegoodbrand.com", "FR", [], "EUR", "shopify"),
    ("Apostrophe Paris", "https://apostrophe-paris.com", "FR", [], "EUR", "shopify"),
    ("Skiim Paris", "https://skiim-paris.com", "FR", [], "EUR", "shopify"),
    ("Isakinparis", "https://isakinparis.com", "FR", [], "EUR", "shopify"),
    ("Karllagerfeldparis", "https://www.karllagerfeldparis.com", "FR", [], "EUR", "shopify"),
    ("Lauravita", "https://lauravita.com", "FR", [], "EUR", "shopify"),
    ("Kleman France", "https://kleman-france.com", "FR", [], "EUR", "shopify"),
    ("Thefrankieshop", "https://thefrankieshop.com", "FR", [], "EUR", "shopify"),
    ("Karllagerfeld", "https://www.karllagerfeld.com", "FR", [], "EUR", "shopify"),
    ("Cocorico", "https://www.cocorico.store", "FR", [], "EUR", "shopify"),
    ("Lecoqsportif", "https://www.lecoqsportif.com", "FR", [], "EUR", "shopify"),
    ("Maisonlabiche", "https://www.maisonlabiche.com", "FR", [], "EUR", "shopify"),
    ("Maison Standards",    "https://maisonstandards.com",         "FR", ["DE","NL","IT","GB"], "EUR", "shopify"),
    ("Loom",                "https://www.loom.fr",                 "FR", ["DE","NL","BE"],      "EUR", "shopify"),
    ("Asphalte",            "https://www.asphalte.com",            "FR", ["DE","NL","BE","IT","GB"],"EUR","shopify"),
    ("Hopaal",              "https://www.hopaal.com",              "FR", ["DE","BE"],           "EUR", "shopify"),
    ("1083",                "https://www.1083.fr",                 "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Atelier Notify",      "https://www.notifyworld.com",         "FR", ["DE","IT","GB"],      "EUR", "shopify"),
    ("Ekyog",               "https://www.ekyog.com",               "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Le Slip Français",    "https://www.leslipfrancais.fr",       "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Patine",              "https://www.patine.fr",               "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Atelier Particulier", "https://www.atelier-particulier.com", "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Sézane",              "https://www.sezane.com",              "FR", ["DE","NL","IT","GB","US"],"EUR","html"),
    ("Bleu Forêt",          "https://www.bleuforet.fr",            "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Le Bourget",          "https://www.lebourget.fr",            "FR", ["DE","BE","IT"],     "EUR", "shopify"),
    ("Saint James",          "https://www.saint-james.com",         "FR", ["DE","NL","GB","US"],"EUR", "shopify"),
    ("Petit Bateau",        "https://www.petit-bateau.com",        "FR", ["DE","NL","GB","IT"],"EUR", "html"),
    ("Comptoir des Cotonniers","https://www.comptoirdescotonniers.com","FR",["DE","BE","IT"], "EUR", "html"),
    ("Bonne Gueule",        "https://www.bonnegueule.fr",          "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Faguo",               "https://faguo-store.com",             "FR", ["DE","BE","IT","GB"],"EUR", "shopify"),
    ("Veja",                "https://www.veja-store.com",          "FR", ["DE","NL","IT","GB","US"],"EUR","shopify"),
    ("La Gentle Factory",   "https://www.lagentlefactory.com",     "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Le T-Shirt Propre",   "https://www.letshirtpropre.com",      "FR", ["DE","BE"],           "EUR", "shopify"),
    ("Maison Cléo",         "https://www.maisoncleo.com",          "FR", ["DE","BE","GB"],     "EUR", "shopify"),
    ("Balzac Paris",        "https://www.balzac-paris.fr",         "FR", ["DE","BE","IT","GB"],"EUR", "shopify"),
    ("Sessùn",              "https://www.sessun.com",              "FR", ["DE","NL","IT","GB"],"EUR", "shopify"),
    ("Vanessa Bruno",       "https://www.vanessabruno.com",        "FR", ["DE","NL","IT","GB","US"],"EUR","shopify"),

    # ═══ ITALY (IT) ═══
    ("Valenza Shop", "https://www.valenza-shop.com", "IT", [], "EUR", "shopify"),
    ("Hordabrand", "https://hordabrand.com", "IT", [], "EUR", "shopify"),
    ("Blanks", "https://www.blanks.it", "IT", [], "EUR", "shopify"),
    ("Shop", "https://shop.simon.com", "IT", [], "EUR", "shopify"),
    ("Thesynerg", "https://thesynerg.com", "IT", [], "EUR", "html"),
    ("Lomalab", "https://lomalab.store", "IT", [], "EUR", "shopify"),
    ("Etiquetteclothiers", "https://www.etiquetteclothiers.com", "IT", [], "EUR", "shopify"),
    ("Aussiesockshop", "https://aussiesockshop.com.au", "IT", [], "EUR", "shopify"),
    ("Nobileitaly", "https://nobileitaly.com", "IT", [], "EUR", "shopify"),
    ("Luxurydenim", "https://luxurydenim.com", "IT", [], "EUR", "shopify"),
    ("Blueblanketjeans", "https://www.blueblanketjeans.com", "IT", [], "EUR", "shopify"),
    ("Vittoriaintimo", "https://www.vittoriaintimo.it", "IT", [], "EUR", "shopify"),
    ("Thebridgefirenze", "https://www.thebridgefirenze.com", "IT", [], "EUR", "shopify"),
    ("Sanroccoitalia", "https://sanroccoitalia.it", "IT", [], "EUR", "shopify"),
    ("Madbagstore", "https://www.madbagstore.com", "IT", [], "EUR", "shopify"),
    ("Fedelicashmere", "https://www.fedelicashmere.com", "IT", [], "EUR", "shopify"),
    ("Santoromilan", "https://santoromilan.com", "IT", [], "EUR", "shopify"),
    ("Tiedeals", "https://tiedeals.com", "IT", [], "EUR", "shopify"),
    ("Piniparma", "https://www.piniparma.com", "IT", [], "EUR", "shopify"),
    ("Slowear",             "https://www.slowear.com",             "IT", ["DE","FR","NL","GB","US"],"EUR","shopify"),
    ("Rifò",                "https://www.rifo-lab.com",            "IT", ["DE","FR","NL","GB"], "EUR", "shopify"),
    ("WRAD",                "https://wradliving.com",              "IT", ["DE","FR","GB"],      "EUR", "shopify"),
    ("Re-Bello",            "https://www.re-bello.com",            "IT", ["DE","FR"],           "EUR", "shopify"),
    ("Quagga",              "https://quagga.it",                   "IT", ["DE","FR"],           "EUR", "shopify"),
    ("Save the Duck",       "https://www.savetheduck.com",         "IT", ["DE","FR","NL","GB","US"],"EUR","shopify"),
    ("Piacenza Cashmere",   "https://www.piacenza1733.com",        "IT", ["DE","FR","GB","US"], "EUR", "shopify"),
    ("Zerobarracento",      "https://zerobarracento.com",          "IT", ["DE","FR","GB"],      "EUR", "shopify"),
    ("Mamma Loves You",     "https://www.mammalovesyou.com",       "IT", ["DE","FR"],           "EUR", "shopify"),
    ("Berto Jeans",         "https://www.bertojeans.com",          "IT", ["DE","FR","GB"],      "EUR", "shopify"),
    ("Yamamay",             "https://www.yamamay.com",             "IT", ["DE","FR","GB"],      "EUR", "html"),
    ("Fanny",               "https://www.fanny.it",                "IT", ["DE","FR"],           "EUR", "shopify"),
    ("OOF Wear",            "https://www.oofwear.com",             "IT", ["DE","FR","GB"],     "EUR", "shopify"),
    ("Iuter",               "https://iuter.com",                   "IT", ["DE","FR","GB"],     "EUR", "shopify"),
    ("Octopus",             "https://www.octopusbrand.com",        "IT", ["DE","FR","GB"],     "EUR", "shopify"),
    ("Doppiaa",             "https://www.doppiaa.it",              "IT", ["DE","FR"],          "EUR", "shopify"),
    ("Tagliatore",          "https://www.tagliatore.com",          "IT", ["DE","FR","GB"],     "EUR", "shopify"),
    ("Officine Generale IT","https://www.officinegenerale.com",    "IT", ["DE","FR","GB","US"],"EUR", "shopify"),
    ("Kontatto",            "https://www.kontatto.com",            "IT", ["DE","FR"],          "EUR", "shopify"),
    ("Massimo Alba",        "https://www.massimoalba.com",         "IT", ["DE","FR","GB"],     "EUR", "shopify"),
    ("Hannes Roether",      "https://www.hannes-roether.com",      "IT", ["DE","AT"],           "EUR", "shopify"),
    ("Pomandere",           "https://www.pomandere.com",           "IT", ["DE","FR","NL"],     "EUR", "shopify"),
    ("Stefanel",            "https://www.stefanel.com",            "IT", ["DE","FR"],          "EUR", "html"),
    ("Aspesi",              "https://www.aspesi.com",              "IT", ["DE","FR","GB","US"],"EUR", "shopify"),
    ("Ten C",               "https://www.tenc.it",                 "IT", ["DE","FR","GB"],     "EUR", "shopify"),

    # ═══ PAKISTAN (PK) ═══
    ("Leftovershub", "https://www.leftovershub.com", "PK", [], "PKR", "shopify"),
    ("Ivarclothing", "https://ivarclothing.com", "PK", [], "PKR", "shopify"),
    ("Alkaramstudio", "https://www.alkaramstudio.com", "PK", [], "PKR", "shopify"),
    ("Fariehaclothing", "https://fariehaclothing.com", "PK", [], "PKR", "shopify"),
    ("Borjan", "https://www.borjan.com.pk", "PK", [], "PKR", "shopify"),
    ("Ottostore", "https://www.ottostore.com", "PK", [], "PKR", "shopify"),
    ("Shopbrumano", "https://shopbrumano.com", "PK", [], "PKR", "shopify"),
    ("Ismailsclothing", "https://www.ismailsclothing.com", "PK", [], "PKR", "shopify"),
    ("Forever21", "https://www.forever21.com", "PK", [], "PKR", "shopify"),
    ("Furorjeans", "https://furorjeans.com", "PK", [], "PKR", "shopify"),
    ("Breakout", "https://breakout.com.pk", "PK", [], "PKR", "shopify"),
    ("Uspoloassn", "https://uspoloassn.in", "PK", [], "PKR", "shopify"),
    ("Uspoloassn", "https://www.uspoloassn.co.uk", "PK", [], "PKR", "shopify"),
    ("Uspoloassn", "https://uspoloassn.com", "PK", [], "PKR", "shopify"),
    ("Monark", "https://monark.com.pk", "PK", [], "PKR", "shopify"),
    ("Jerseyncotton", "https://jerseyncotton.com.pk", "PK", [], "PKR", "shopify"),
    ("Brandsrepublicstore", "https://www.brandsrepublicstore.com", "PK", [], "PKR", "shopify"),
    ("Radstore", "https://radstore.pk", "PK", [], "PKR", "shopify"),
    ("Avocado", "https://avocado.pk", "PK", [], "PKR", "shopify"),
    ("Charcoal", "https://charcoal.com.pk", "PK", [], "PKR", "shopify"),
    ("Thebrown", "https://thebrown.store", "PK", [], "PKR", "shopify"),
    ("Bluejeans", "https://bluejeans.nyc", "PK", [], "PKR", "shopify"),
    ("Aquila", "https://aquila.pk", "PK", [], "PKR", "shopify"),
    ("Denims", "https://denims.pk", "PK", [], "PKR", "shopify"),
    ("Alohas", "https://alohas.com", "PK", [], "PKR", "shopify"),
    ("Mertra", "https://mertra.com", "PK", [], "PKR", "shopify"),
    ("Wearableoutfit", "https://wearableoutfit.com", "PK", [], "PKR", "shopify"),
    ("Prelovedlabels", "https://prelovedlabels.com", "PK", [], "PKR", "shopify"),
    ("Ismailfarid", "https://www.ismailfarid.com", "PK", [], "PKR", "shopify"),
    ("Shopelegancia", "https://www.shopelegancia.com", "PK", [], "PKR", "shopify"),
    ("Eternitymen", "https://www.eternitymen.com", "PK", [], "PKR", "shopify"),
    ("Studiobytcs", "https://www.studiobytcs.com", "PK", [], "PKR", "shopify"),
    ("Uniworthshop", "https://uniworthshop.com", "PK", [], "PKR", "shopify"),
    ("Nomadthelabel", "https://nomadthelabel.com", "PK", [], "PKR", "shopify"),
    ("Misssixty", "https://misssixty.com", "PK", [], "PKR", "shopify"),
    ("Stringnthread", "https://www.stringnthread.com", "PK", [], "PKR", "shopify"),
    ("Shopatmeme", "https://shopatmeme.com", "PK", [], "PKR", "shopify"),
    ("Thehangerpakistan", "https://thehangerpakistan.com", "PK", [], "PKR", "shopify"),
    ("Turbobrandsfactory", "https://turbobrandsfactory.com", "PK", [], "PKR", "shopify"),
    ("Elitecapshop", "https://elitecapshop.com", "PK", [], "PKR", "shopify"),
    ("Niftycaps", "https://niftycaps.com", "PK", [], "PKR", "shopify"),
    ("World", "https://world.benetton.com", "PK", [], "PKR", "shopify"),
    ("Thecottonleaf", "https://thecottonleaf.pk", "PK", [], "PKR", "shopify"),
    ("Rtwcreation", "https://rtwcreation.com", "PK", [], "PKR", "shopify"),
    ("Houseofleather", "https://www.houseofleather.pk", "PK", [], "PKR", "shopify"),
    ("Shopmanto", "https://www.shopmanto.com", "PK", [], "PKR", "shopify"),
    ("Dynastyfabrics", "https://www.dynastyfabrics.com", "PK", [], "PKR", "shopify"),
    ("Amoosajeesons", "https://www.amoosajeesons.com", "PK", [], "PKR", "shopify"),
    ("1947Clothing", "https://1947clothing.com", "PK", [], "PKR", "shopify"),
    ("Thecambridgeshop", "https://thecambridgeshop.com", "PK", [], "PKR", "shopify"),
    ("Focusclothing", "https://focusclothing.pk", "PK", [], "PKR", "shopify"),
    ("Beoneshopone", "https://beoneshopone.com", "PK", [], "PKR", "shopify"),
    ("Fittedshop", "https://fittedshop.com", "PK", [], "PKR", "shopify"),
    ("Zeenwoman", "https://zeenwoman.com", "PK", [], "PKR", "shopify"),
    ("Shahzebsaeed", "https://shahzebsaeed.com", "PK", [], "PKR", "shopify"),
    ("Lawrencepur", "https://lawrencepur.com", "PK", [], "PKR", "shopify"),
    ("Poshnotch", "https://poshnotch.com", "PK", [], "PKR", "shopify"),
    ("Sudathi", "https://sudathi.com", "PK", [], "PKR", "shopify"),
    # Most major Pakistani brands run on Magento, OpenCart, or custom platforms.
    # JSON-LD HTML fallback handles those. Smaller / newer brands often use Shopify.
    ("Generation",          "https://generation.com.pk",           "PK", [], "PKR", "shopify"),
    ("Khaadi",              "https://pk.khaadi.com",               "PK", [], "PKR", "html"),
    ("Sapphire",            "https://pk.sapphireonline.pk",        "PK", [], "PKR", "html"),
    ("Outfitters",          "https://outfitters.com.pk",           "PK", [], "PKR", "shopify"),
    ("Beechtree",           "https://beechtree.pk",                "PK", [], "PKR", "shopify"),
    ("Gul Ahmed",           "https://www.gulahmedshop.com",        "PK", [], "PKR", "html"),
    ("Cross Stitch",        "https://www.crossstitch.pk",          "PK", [], "PKR", "shopify"),
    ("Ego",                 "https://ego.com.pk",                  "PK", [], "PKR", "shopify"),
    ("Edenrobe",            "https://edenrobe.com",                "PK", [], "PKR", "html"),
    ("Almirah",             "https://almirah.com.pk",              "PK", [], "PKR", "shopify"),
    ("Limelight",           "https://www.limelight.pk",            "PK", [], "PKR", "html"),
    ("Junaid Jamshed",      "https://www.junaidjamshed.com",       "PK", [], "PKR", "html"),
    ("Maria B",             "https://www.mariab.pk",               "PK", [], "PKR", "html"),
    ("Sana Safinaz",        "https://www.sanasafinaz.com",         "PK", [], "PKR", "html"),
    ("Nishat Linen",        "https://nishatlinen.com",             "PK", [], "PKR", "html"),
    ("Bonanza Satrangi",    "https://www.bonanzasatrangi.com",     "PK", [], "PKR", "html"),
    ("Alkaram Studio",      "https://alkaramstudio.com",           "PK", [], "PKR", "html"),
    ("Sefam Group (HSY)",   "https://www.hsy.com",                 "PK", [], "PKR", "shopify"),
    ("Asim Jofa",           "https://www.asimjofa.com",            "PK", [], "PKR", "html"),
    ("Zellbury",            "https://www.zellbury.com",            "PK", [], "PKR", "shopify"),
    ("So Kamal",            "https://sokamal.com",                 "PK", [], "PKR", "html"),
    ("Image Fabrics",       "https://imagefabric.com",             "PK", [], "PKR", "shopify"),
    ("Saadia Asad",         "https://saadiaasad.com",              "PK", [], "PKR", "shopify"),
    ("Ittehad Textiles",    "https://www.ittehadtextiles.com",     "PK", [], "PKR", "html"),
    ("Charizma",            "https://charizma.com.pk",             "PK", [], "PKR", "shopify"),
    ("Mausummery",          "https://mausummery.com",              "PK", [], "PKR", "html"),
]


# ───────────────────────── DATA MODEL ─────────────────────────────────────────

@dataclass
class Listing:
    brand: str
    title: str
    price: float
    currency: str
    url: str
    image_url: str
    sizes: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    category: str = "other"
    available: bool = True
    country: str = ""
    gender: str = "unisex"

    def as_dict(self) -> dict:
        return asdict(self)


# ───────────────────────── HTTP / SESSION ─────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BLOCKED_STATUSES = {401, 403, 429, 503}
TIMEOUT = 15

# Configure your proxies here. Use a rotating proxy service.
PROXIES = {
    # "http": "http://user:pass@proxy-server:port",
    # "https": "http://user:pass@proxy-server:port",
}

logger = logging.getLogger("clothing_scraper")


class ScraperBlocked(Exception):
    """Raised when a brand's site refuses or breaks. Caller skips and moves on."""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if PROXIES:
        s.proxies.update(PROXIES)
    return s


# ───────────────────────── PARSING HELPERS ────────────────────────────────────

def categorize(title: str) -> str:
    t = title.lower()
    rules = [
        ("kurta",      ["kurta", "kurti"]),
        ("shalwar",    ["shalwar", "kameez", "salwar"]),
        ("dupatta",    ["dupatta"]),
        ("saree",      ["saree", "sari"]),
        ("lawn-suit",  ["lawn", "unstitched"]),
        ("t-shirt",    ["t-shirt", " tee", "tee ", "tshirt"]),
        ("shirt",      ["shirt", "blouse", "button-down", "oxford"]),
        ("hoodie",     ["hoodie", "hooded"]),
        ("sweatshirt", ["sweatshirt", "crewneck"]),
        ("sweater",    ["sweater", "jumper", "knit", "cardigan", "pullover"]),
        ("jacket",     ["jacket", "blazer", "coat", "parka", "vest", "anorak"]),
        ("jeans",      ["jeans", "denim"]),
        ("pants",      ["pants", "trouser", "chino", "joggers", "leggings"]),
        ("shorts",     ["shorts"]),
        ("skirt",      ["skirt"]),
        ("dress",      ["dress", "gown", "maxi"]),
        ("shoes",      ["shoe", "sneaker", "boot", "trainer", "loafer", "sandal"]),
        ("hat",        ["hat", "cap", "beanie"]),
        ("bag",        ["bag", "backpack", "tote"]),
        ("scarf",      ["scarf", "shawl", "stole"]),
        ("accessory",  ["belt", "sock", "glove", "tie"]),
        ("underwear",  ["underwear", "boxer", "brief", "bra"]),
    ]
    for category, keywords in rules:
        if any(k in t for k in keywords):
            return category
    return "other"


_FEMALE_PATH_HINTS = (
    "/women", "/womens", "/woman", "/ladies", "/female",
    "/girls", "/girl-", "-womens-", "-women-", "/her/", "/for-her",
)
_MALE_PATH_HINTS = (
    "/men", "/mens", "/male", "/boys", "/boy-", "-mens-", "-men-",
    "/him/", "/for-him", "/guys",
)
_FEMALE_CATEGORIES = {
    "dress", "skirt", "saree", "kurta", "dupatta", "lawn-suit",
}
_MALE_CATEGORIES: set[str] = set()
_FEMALE_TITLE_WORDS = (
    r"women", r"woman", r"womens", r"ladies", r"female", r"girls?",
    r"dresses?", r"skirts?", r"blouses?", r"gowns?", r"sarees?", r"saris?",
    r"kurtas?", r"kurtis?", r"dupattas?", r"lingerie", r"bikinis?",
    r"bras?", r"panties?", r"camisoles?", r"leggings?",
)
_MALE_TITLE_WORDS = (
    r"mens?", r"man", r"male", r"boys?", r"guys?", r"gentleman",
    r"boxers?", r"neckties?", r"tuxedos?",
)
_FEMALE_RE = re.compile(r"\b(" + "|".join(_FEMALE_TITLE_WORDS) + r")\b", re.IGNORECASE)
_MALE_RE = re.compile(r"\b(" + "|".join(_MALE_TITLE_WORDS) + r")\b", re.IGNORECASE)


def infer_gender(title: str, url: str, category: str = "") -> str:
    """Infer gender. Returns one of {'male', 'female', 'unisex'}.

    Order of signals (most reliable first):
      1. URL path (e.g. /collections/women/, /shop/mens/)
      2. Word-boundary regex on the title — avoids the 'women contains men' trap
      3. Category fallback (a dress is female regardless of title wording)
    Defaults to 'unisex' when no signal fires.
    """
    url_l = url.lower()
    if any(h in url_l for h in _FEMALE_PATH_HINTS):
        return "female"
    if any(h in url_l for h in _MALE_PATH_HINTS) and not any(
        w in url_l for w in ("women", "ladies", "female", "girl")
    ):
        return "male"

    t = title or ""
    if _FEMALE_RE.search(t):
        return "female"
    if _MALE_RE.search(t):
        return "male"

    cat_l = (category or "").lower()
    if cat_l in _FEMALE_CATEGORIES:
        return "female"
    if cat_l in _MALE_CATEGORIES:
        return "male"

    return "unisex"


def normalize_gender(g) -> str:
    """Canonicalize any incoming gender value to {'male','female','unisex'}."""
    if not g:
        return "unisex"
    s = str(g).strip().lower()
    if s in ("male", "men", "mens", "m", "man"):
        return "male"
    if s in ("female", "women", "womens", "f", "woman", "ladies"):
        return "female"
    if s == "unisex":
        return "unisex"
    return "unisex"


def _looks_like_size(value: str) -> bool:
    v = (value or "").upper().strip()
    if not v:
        return False
    size_tokens = {
        "XXS","XS","S","M","L","XL","XXL","XXXL","2XL","3XL","4XL","5XL",
        "SMALL","MEDIUM","LARGE","ONE SIZE","OS","ONESIZE","FREE SIZE",
    }
    if v in size_tokens:
        return True
    cleaned = v.replace("W","").replace("L","").replace(".","").replace("/","")
    if cleaned and cleaned.isdigit():
        return True
    return False


# ───────────────────────── SHOPIFY SCRAPER ────────────────────────────────────

def parse_shopify_product(raw: dict, brand: str, base_url: str,
                          currency: str, country: str) -> Optional[Listing]:
    try:
        variants = raw.get("variants") or []
        if not variants:
            return None

        prices = []
        for v in variants:
            p = v.get("price")
            if p is not None:
                try:
                    prices.append(float(p))
                except (TypeError, ValueError):
                    pass
        if not prices:
            return None
        price = min(prices)

        sizes = sorted({
            v.get("option1","").strip()
            for v in variants
            if v.get("option1") and _looks_like_size(v["option1"])
        })

        colors: list[str] = []
        for opt in raw.get("options") or []:
            name = (opt.get("name") or "").lower()
            if "color" in name or "colour" in name:
                colors = [c for c in (opt.get("values") or []) if c]
                break

        handle = raw.get("handle") or ""
        url = f"{base_url.rstrip('/')}/products/{handle}"

        images = raw.get("images") or []
        image_url = images[0].get("src","") if images else ""

        available = any(v.get("available") for v in variants)
        title = (raw.get("title") or "Untitled").strip()
        category = categorize(title)

        return Listing(
            brand=brand, title=title, price=round(price, 2), currency=currency,
            url=url, image_url=image_url,
            sizes=list(sizes) if sizes else ["One Size"],
            colors=colors if colors else ["Default"],
            category=category,
            available=available, country=country,
            gender=normalize_gender(infer_gender(title, url, category)),
        )
    except Exception as e:
        logger.debug(f"  [parse-error] {brand}: {e}")
        return None


def scrape_shopify(session: requests.Session, brand: str, base_url: str,
                   currency: str, country: str,
                   max_pages: int = 5, per_page: int = 250) -> list[Listing]:
    listings: list[Listing] = []
    for page in range(1, max_pages + 1):
        url = f"{base_url.rstrip('/')}/products.json?limit={per_page}&page={page}"
        try:
            resp = session.get(url, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise ScraperBlocked(f"network error: {type(e).__name__}")

        if resp.status_code in BLOCKED_STATUSES:
            raise ScraperBlocked(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            if page == 1:
                raise ScraperBlocked(f"HTTP {resp.status_code}")
            break

        try:
            payload = resp.json()
        except ValueError:
            raise ScraperBlocked("non-JSON (likely HTML challenge page)")

        items = payload.get("products") or []
        if not items:
            break
        for raw in items:
            l = parse_shopify_product(raw, brand, base_url, currency, country)
            if l:
                listings.append(l)
        if len(items) < per_page:
            break

    if not listings:
        raise ScraperBlocked("no products parsed")
    return listings


# ───────────────────────── HTML FALLBACK SCRAPER ──────────────────────────────
# Many big brands aren't on Shopify (Magento, custom). Almost all of them emit
# JSON-LD <script type="application/ld+json"> Product entries because Google
# Shopping requires it. We harvest those.

JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_jsonld_products(html: str) -> list[dict]:
    products: list[dict] = []
    for match in JSONLD_RE.findall(html):
        try:
            data = json.loads(match.strip())
        except json.JSONDecodeError:
            continue
        candidates = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                candidates = data["@graph"]
            else:
                candidates = [data]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            t = c.get("@type")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                products.append(c)
    return products


def _parse_jsonld_product(p: dict, brand: str, base_url: str,
                         currency: str, country: str) -> Optional[Listing]:
    try:
        title = p.get("name") or ""
        if not title:
            return None

        offers = p.get("offers")
        price = None
        cur = currency
        avail_str = ""
        if isinstance(offers, dict):
            price = offers.get("price") or offers.get("lowPrice")
            cur = offers.get("priceCurrency") or currency
            avail_str = (offers.get("availability") or "").lower()
        elif isinstance(offers, list) and offers:
            first = offers[0]
            if isinstance(first, dict):
                price = first.get("price") or first.get("lowPrice")
                cur = first.get("priceCurrency") or currency
                avail_str = (first.get("availability") or "").lower()
        if price is None:
            return None
        try:
            price = float(str(price).replace(",", ""))
        except (TypeError, ValueError):
            return None

        url = p.get("url") or ""
        if url and not url.startswith("http"):
            url = base_url.rstrip("/") + "/" + url.lstrip("/")
        if not url:
            url = base_url

        img = p.get("image") or ""
        if isinstance(img, list):
            img = img[0] if img else ""
        if isinstance(img, dict):
            img = img.get("url", "")

        available = "outofstock" not in avail_str.replace(" ", "").replace("/", "")

        category = categorize(title)
        return Listing(
            brand=brand, title=title.strip(), price=round(price, 2),
            currency=cur or currency, url=url, image_url=img or "",
            sizes=["See site"], colors=["See site"],
            category=category, available=available, country=country,
            gender=normalize_gender(infer_gender(title.strip(), url, category)),
        )
    except Exception as e:
        logger.debug(f"  [jsonld-parse-error] {brand}: {e}")
        return None


def scrape_html(session: requests.Session, brand: str, base_url: str,
                currency: str, country: str) -> list[Listing]:
    candidate_paths = ["/", "/collections/all", "/shop", "/products",
                       "/category", "/catalog", "/all-products",
                       "/women", "/men", "/new-arrivals"]
    listings: list[Listing] = []
    last_error = "no JSON-LD products found"

    for path in candidate_paths:
        url = base_url.rstrip("/") + path
        try:
            resp = session.get(url, timeout=TIMEOUT)
        except requests.RequestException as e:
            last_error = f"network error: {type(e).__name__}"
            continue
        if resp.status_code in BLOCKED_STATUSES:
            raise ScraperBlocked(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            continue

        products = _extract_jsonld_products(resp.text)
        for p in products:
            l = _parse_jsonld_product(p, brand, base_url, currency, country)
            if l:
                listings.append(l)
        if listings:
            break

    if not listings:
        raise ScraperBlocked(last_error)
    return listings


# ───────────────────────── DYNAMIC BRAND REGISTRATION ─────────────────────────
# Thread-safe runtime addition of brands discovered by ml_scraper.

_brands_lock = threading.Lock()


def register_brand(name: str, base_url: str, country: str,
                   also_ships_to: list[str] | None = None,
                   currency: str = "USD", platform: str = "shopify") -> bool:
    """
    Add a brand to the in-memory BRANDS list at runtime.
    Returns True if added, False if the URL was already present.
    Thread-safe.
    """
    with _brands_lock:
        # Dedup by base_url
        existing_urls = {b[1] for b in BRANDS}
        if base_url in existing_urls:
            return False
        entry = (name, base_url, country.upper(), also_ships_to or [],
                 currency, platform)
        BRANDS.append(entry)
        return True


# ───────────────────────── ORCHESTRATION ──────────────────────────────────────

def brands_for_country(country: str):
    cc = country.upper()
    out = []
    with _brands_lock:
        for entry in BRANDS:
            name, url, primary, also, currency, platform = entry
            if cc == primary or cc in also:
                out.append(entry)
    return out


def matches_query(listing: Listing, query: str) -> bool:
    if not query:
        return True
    q = query.lower().replace("-", " ").strip()
    tokens = [t for t in q.split() if t]
    haystack = " ".join([
        listing.title.lower(), listing.brand.lower(),
        " ".join(c.lower() for c in listing.colors),
        listing.category.lower(),
    ])
    return all(t in haystack for t in tokens)


def scrape_one(entry, country: str):
    name, base_url, _, _, currency, platform = entry
    session = make_session()
    t0 = time.time()
    try:
        if platform == "shopify":
            try:
                items = scrape_shopify(session, name, base_url, currency, country)
            except ScraperBlocked:
                # If /products.json blocked, try JSON-LD HTML as fallback
                items = scrape_html(session, name, base_url, currency, country)
        else:
            items = scrape_html(session, name, base_url, currency, country)
        return (name, items, None, time.time() - t0)
    except ScraperBlocked as e:
        return (name, None, str(e), time.time() - t0)
    except Exception as e:
        return (name, None, f"{type(e).__name__}: {e}", time.time() - t0)


def run(country: str, query: str = "", max_results: int = 100,
        workers: int = 6, verbose: bool = True):
    targets = brands_for_country(country)
    if not targets:
        if verbose:
            print(f"\n⚠  No brands configured for '{country}'.\n")
        return [], [], []

    if verbose:
        print(f"\n→ {len(targets)} brands available in {country.upper()}. Scraping…\n")

    successful: list[tuple[str, int]] = []
    skipped: list[tuple[str, str]] = []
    all_listings: list[Listing] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(scrape_one, e, country.upper()) for e in targets]
        for fut in concurrent.futures.as_completed(futs):
            name, items, err, dt = fut.result()
            if err or items is None:
                skipped.append((name, err or "unknown"))
                if verbose:
                    print(f"  ✗ {name:28} skipped — {err}  ({dt:.1f}s)")
            else:
                successful.append((name, len(items)))
                all_listings.extend(items)
                if verbose:
                    print(f"  ✓ {name:28} {len(items):4} items  ({dt:.1f}s)")

    matched = [l for l in all_listings if matches_query(l, query)]
    matched.sort(key=lambda l: (l.price, l.brand))
    return matched[:max_results], successful, skipped


# ───────────────────────── DISPLAY / EXPORT ───────────────────────────────────

CURRENCY_SYM = {"USD":"$","GBP":"£","EUR":"€","CAD":"C$","AUD":"A$","PKR":"Rs."}


def print_results(listings, successful, skipped, country: str, query: str):
    line = "─" * 78
    print(f"\n{line}")
    print(f"  RESULTS — query={query!r}, country={country.upper()}")
    print(f"  {len(listings)} listings shown  │  "
          f"{len(successful)} brands scraped  │  {len(skipped)} skipped")
    print(line)

    if listings:
        for i, l in enumerate(listings, 1):
            sym = CURRENCY_SYM.get(l.currency, l.currency + " ")
            sizes = ", ".join(l.sizes[:6]) + (" …" if len(l.sizes) > 6 else "")
            colors = ", ".join(l.colors[:4]) + (" …" if len(l.colors) > 4 else "")
            stock = "" if l.available else "  [OUT OF STOCK]"
            print(f"\n  [{i:>3}] {l.brand}  ·  {l.category}{stock}")
            print(f"        {l.title}")
            print(f"        {sym}{l.price:.2f} {l.currency}")
            print(f"        sizes:  {sizes}")
            print(f"        colors: {colors}")
            print(f"        link:   {l.url}")
    else:
        print("\n  No matching listings.")
        if not successful:
            print("  Every brand site refused or returned no parseable data.")
            print("  → Try running this from a residential IP (your laptop on home wifi).")
            print("  → Cloud / datacenter / sandbox IPs get blanket-blocked by Cloudflare.")

    print(f"\n{line}")
    if successful:
        print(f"  ✓ Scraped successfully ({len(successful)}):")
        for n, c in successful:
            print(f"      • {n}  ({c} items)")
    if skipped:
        print(f"  ✗ Skipped ({len(skipped)}):")
        for n, why in skipped:
            print(f"      • {n}  — {why}")
    print(line + "\n")


def write_json(path: str, listings: list[Listing]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([l.as_dict() for l in listings], f, indent=2, ensure_ascii=False)
    print(f"  → wrote {len(listings)} listings to {path}")


def write_csv(path: str, listings: list[Listing]) -> None:
    if not listings:
        print(f"  (nothing to write to {path})")
        return
    cols = ["brand","title","price","currency","category","available",
            "sizes","colors","country","url","image_url"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for l in listings:
            w.writerow([l.brand, l.title, l.price, l.currency, l.category,
                        l.available, "|".join(l.sizes), "|".join(l.colors),
                        l.country, l.url, l.image_url])
    print(f"  → wrote {len(listings)} listings to {path}")


# ───────────────────────── VERIFY MODE ────────────────────────────────────────
# Fast probe: hits each brand's catalogue endpoint with a single GET, parses just
# enough to confirm products are reachable. Doesn't download every page like the
# full scraper. Use to figure out which brands actually accept your requests
# from your network before running the real scrape.

def verify_brand(entry, country: str, timeout: int = 10) -> dict:
    """Probe one brand. Returns a dict with status info."""
    name, base_url, primary, also, currency, platform = entry
    session = make_session()
    result = {
        "name": name, "url": base_url, "country": primary, "platform": platform,
        "ok": False, "status": None, "items_seen": 0, "elapsed_s": 0.0,
        "reason": "", "method": "",
    }
    t0 = time.time()
    try:
        if platform == "shopify":
            probe_url = f"{base_url.rstrip('/')}/products.json?limit=5"
            try:
                resp = session.get(probe_url, timeout=timeout)
            except requests.RequestException as e:
                result["reason"] = f"network: {type(e).__name__}"
                return _finalize_verify(result, t0)
            result["status"] = resp.status_code
            result["method"] = "shopify"
            if resp.status_code in BLOCKED_STATUSES:
                result["reason"] = f"blocked HTTP {resp.status_code}"
                # try HTML fallback before giving up
                return _verify_html_probe(result, session, base_url, timeout, t0)
            if resp.status_code != 200:
                result["reason"] = f"HTTP {resp.status_code}"
                return _verify_html_probe(result, session, base_url, timeout, t0)
            try:
                data = resp.json()
            except ValueError:
                result["reason"] = "non-JSON (HTML challenge?)"
                return _verify_html_probe(result, session, base_url, timeout, t0)
            items = data.get("products") or []
            result["items_seen"] = len(items)
            if items:
                result["ok"] = True
                result["reason"] = f"shopify ok ({len(items)} items in probe)"
            else:
                result["reason"] = "shopify returned 0 products"
                return _verify_html_probe(result, session, base_url, timeout, t0)
        else:
            return _verify_html_probe(result, session, base_url, timeout, t0)
    except Exception as e:
        result["reason"] = f"unexpected: {type(e).__name__}: {e}"
    return _finalize_verify(result, t0)


def _verify_html_probe(result: dict, session: requests.Session, base_url: str,
                       timeout: int, t0: float) -> dict:
    """Try the JSON-LD HTML path as a fallback / primary."""
    for path in ["/", "/collections/all", "/shop", "/products"]:
        try:
            resp = session.get(base_url.rstrip("/") + path, timeout=timeout)
        except requests.RequestException as e:
            result["reason"] = f"network: {type(e).__name__}"
            continue
        if resp.status_code in BLOCKED_STATUSES:
            result["status"] = resp.status_code
            result["reason"] = f"blocked HTTP {resp.status_code}"
            return _finalize_verify(result, t0)
        if resp.status_code != 200:
            result["status"] = resp.status_code
            continue
        products = _extract_jsonld_products(resp.text)
        if products:
            result["ok"] = True
            result["status"] = 200
            result["method"] = "html-jsonld"
            result["items_seen"] = len(products)
            result["reason"] = f"json-ld ok ({len(products)} products on {path})"
            return _finalize_verify(result, t0)
    if not result["reason"]:
        result["reason"] = "no products found on any common path"
    return _finalize_verify(result, t0)


def _finalize_verify(result: dict, t0: float) -> dict:
    result["elapsed_s"] = round(time.time() - t0, 2)
    return result


def verify_country(country: str, workers: int = 8, timeout: int = 10) -> list[dict]:
    """Probe every brand for the given country and return per-brand results."""
    targets = brands_for_country(country)
    print(f"\n→ Verifying {len(targets)} brands for country {country.upper()} "
          f"(timeout={timeout}s, workers={workers})…\n")
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(verify_brand, e, country.upper(), timeout) for e in targets]
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            results.append(r)
            mark = "✓" if r["ok"] else "✗"
            status = str(r["status"] or "—")
            method = r["method"] or "-"
            print(f"  {mark} {r['name']:30} {status:>5}  {method:11} "
                  f"{r['elapsed_s']:>5.1f}s  {r['reason']}")
    results.sort(key=lambda r: (not r["ok"], r["name"]))
    return results


def print_verify_summary(country: str, results: list[dict]) -> None:
    line = "─" * 78
    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    print(f"\n{line}")
    print(f"  VERIFY SUMMARY — country={country.upper()}")
    print(f"  {len(ok)} working  │  {len(bad)} blocked / unreachable  │  "
          f"{len(results)} total")
    print(line)
    if ok:
        print(f"\n  ✓ Working brands ({len(ok)}):")
        for r in ok:
            print(f"      • {r['name']}  ({r['method']}, {r['items_seen']} probed)")
    if bad:
        print(f"\n  ✗ Skipped ({len(bad)}):")
        for r in bad:
            print(f"      • {r['name']}  — {r['reason']}")
    print()


def write_verify_json(path: str, country: str, results: list[dict]) -> None:
    payload = {
        "country": country.upper(),
        "checked_at": int(time.time()),
        "total": len(results),
        "ok": sum(1 for r in results if r["ok"]),
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"  → wrote verify report to {path}")


# ───────────────────────── CLI ────────────────────────────────────────────────

SUPPORTED_COUNTRIES = ["US","CA","GB","IE","AU","DE","NL","FR","IT","PK"]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape clothing listings from real brand sites by country.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--country","-c", help=f"ISO country code: {', '.join(SUPPORTED_COUNTRIES)}")
    p.add_argument("--query","-q", default="", help="Search query, e.g. 'black t-shirt' (empty = all).")
    p.add_argument("--max", type=int, default=100, help="Max listings to display.")
    p.add_argument("--workers", type=int, default=6, help="Concurrent brand scrapes.")
    p.add_argument("--json", metavar="PATH", help="Also save matched listings to JSON.")
    p.add_argument("--csv",  metavar="PATH", help="Also save matched listings to CSV.")
    p.add_argument("--list-countries", action="store_true",
                   help="Print supported countries and brand counts.")
    p.add_argument("--verify", action="store_true",
                   help="Probe each brand once and report which respond. "
                        "Use with --country, or with --verify-all for every country.")
    p.add_argument("--verify-all", action="store_true",
                   help="Run --verify across every supported country.")
    p.add_argument("--verify-timeout", type=int, default=10,
                   help="Per-request timeout (seconds) for verify mode.")
    p.add_argument("-v","--verbose", action="store_true", help="Verbose logs.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.list_countries:
        print("Supported countries:")
        for cc in SUPPORTED_COUNTRIES:
            n = len(brands_for_country(cc))
            print(f"  {cc}  ({n} brands available)")
        return 0

    # ─── Verify mode (no scraping) ───
    if args.verify_all:
        all_results = {}
        for cc in SUPPORTED_COUNTRIES:
            results = verify_country(cc, workers=args.workers,
                                     timeout=args.verify_timeout)
            print_verify_summary(cc, results)
            all_results[cc] = results
        # Combined report
        line = "═" * 78
        print(f"\n{line}")
        print("  COMBINED VERIFY REPORT")
        print(line)
        for cc in SUPPORTED_COUNTRIES:
            rs = all_results[cc]
            ok = sum(1 for r in rs if r["ok"])
            print(f"  {cc}: {ok}/{len(rs)} working")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump({
                    "checked_at": int(time.time()),
                    "by_country": all_results,
                }, f, indent=2, ensure_ascii=False)
            print(f"\n  → wrote full verify report to {args.json}")
        return 0

    if args.verify:
        country = args.country
        if not country:
            print(f"Supported countries: {', '.join(SUPPORTED_COUNTRIES)}")
            country = input("Enter country code to verify: ").strip()
        if country.upper() not in SUPPORTED_COUNTRIES:
            print(f"\n⚠  '{country}' not supported.")
            return 1
        results = verify_country(country, workers=args.workers,
                                 timeout=args.verify_timeout)
        print_verify_summary(country, results)
        if args.json:
            write_verify_json(args.json, country, results)
        return 0

    country = args.country
    query = args.query

    if not country:
        print(f"Supported countries: {', '.join(SUPPORTED_COUNTRIES)}")
        country = input("Enter country code: ").strip()
    if country.upper() not in SUPPORTED_COUNTRIES:
        print(f"\n⚠  '{country}' not supported. "
              f"Choose from: {', '.join(SUPPORTED_COUNTRIES)}")
        return 1
    if not query:
        query = input("What are you looking for? (empty = all items): ").strip()

    listings, successful, skipped = run(
        country=country, query=query, max_results=args.max,
        workers=args.workers, verbose=True,
    )
    print_results(listings, successful, skipped, country, query)

    if args.json:
        write_json(args.json, listings)
    if args.csv:
        write_csv(args.csv, listings)

    return 0


if __name__ == "__main__":
    sys.exit(main())