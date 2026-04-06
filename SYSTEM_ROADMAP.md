# System Roadmap

## Goal

Build a system that regularly collects online information about U.S. officials and legislators who:

- mention Taiwan
- mention Formosa
- participate in Taiwan-related activities
- sponsor or support Taiwan-related legislation
- are mentioned in official or media reporting connected to Taiwan

The system should be person-centered, event-centered, and update on a recurring basis.

## Core Principles

### 1. People first

The system is anchored on people pages.

Each person page should represent one identifiable U.S. public figure, including:

- federal executive officials
- federal senators
- federal representatives
- state governors
- other state executive officials
- state senators
- state representatives
- territorial officials
- territorial delegates and local legislators
- former officials and former legislators

### 2. Events as the main unit

The main information unit is an event, not an article.

One event may have:

- multiple participants
- multiple official sources
- multiple media sources
- multiple social sources

Examples:

- a delegation visit to Taiwan
- a hearing mentioning Taiwan
- a press statement on Taiwan
- a social media post about Taiwan
- a Taiwan-related trade or security meeting

### 3. Official sources first

Source priority should remain:

1. U.S. government official pages
2. official Taiwan government pages
3. official legislature / agency / bill pages
4. social media accounts
5. major media
6. Wikipedia as supplemental seed / enrichment only

`Official page` must mean government websites only.

### 4. Scheduled and repeatable

The system should not rely on one-time manual import.

It should support recurring jobs that:

- refresh people rosters
- refresh official pages
- refresh legislation
- refresh event sources
- reconcile current vs former status
- attach new events to the right people

## Data Model Direction

## People

Each person record should aim to store:

- English display name
- Chinese display name
- full legal name
- current / former status
- federal / state / territorial level
- branch
- office title
- department
- subdepartment
- lower unit
- official page
- Wikipedia page
- social profiles
- portrait URL
- portrait source
- background fields

Background fields should include:

- date of birth
- place of birth
- education
- past experience
- party
- district
- committees
- prior government service

## Offices and hierarchy

Executive people should be navigable as:

- department
- subdepartment
- lower unit
- official

Legislative people should be navigable as:

- state / chamber / status / person
- or federal chamber / state / status / person

## Events

Each event should support:

- title
- event date
- summary
- event type
- participants
- official sources
- media sources
- social sources
- Taiwan relevance markers

Event types should include at least:

- official statement
- media report
- social post
- visit
- meeting
- hearing
- legislation-related action
- trade / security / diplomacy activity

## Legislation

Legislation should be a parallel track separate from events, but linkable to people.

Each legislation record should aim to store:

- bill number
- congress or state session
- title
- date
- chamber
- status
- summary
- sponsors
- official bill page
- additional topic tags

For historical Congress data:

- use the Excel `TW > 0` seed as the page backbone
- enrich with `Congress.gov` where possible
- do not expand beyond the Excel historical scope unless explicitly needed later

## Source Intake Rules

## A. Roster sources

Use these to build and refresh people pages:

- federal official rosters
- federal legislator rosters
- state official rosters
- state legislator rosters
- territorial official rosters
- territorial legislator rosters

Priority:

1. official government roster
2. official agency or legislature directory
3. Wikipedia only if official source is blocked or incomplete

## B. Event sources

Use these to build Taiwan-related events:

- official U.S. government statements
- Taiwan official government pages
- official legislative releases
- official bill and hearing pages
- official social accounts
- major media pages

Current high-value Taiwan-facing sources:

- `president.gov.tw`
- `mofa.gov.tw`
- `cna.com.tw`

## C. Social sources

Social intake should focus on official or high-confidence official accounts.

Priority:

1. official website linked account
2. government page linked account
3. verified / high-confidence candidate
4. manual confirmation

## Matching Rules

## Person matching

When an article or post is ingested:

- scan for names already in the system
- match against English names
- match against Chinese names
- match against aliases
- match against office-title aliases where needed

But only attach:

- U.S. officials
- U.S. legislators
- territorial officials / delegates if included in scope

Do not attach non-U.S. figures as participants.

## Taiwan relevance

Content should be included if:

- it explicitly mentions `Taiwan`
- it explicitly mentions `Formosa`
- it is a Taiwan-related official activity even if `Taiwan` is implied by the source context

Examples:

- visit to Taiwan
- reception by Taiwan president
- meeting with Taiwan foreign ministry
- Taiwan defense budget comments
- Taiwan trade agreement comments
- Taiwan Strait / cross-Strait remarks

## Event merging

If multiple articles describe the same underlying occurrence:

- keep one event
- attach all participants to that one event
- attach all sources to that one event

## Scheduled Jobs

The long-term recurring jobs should be:

### 1. Roster refresh

Purpose:

- update current officials and legislators
- mark missing incumbents as former
- create new people pages for new incumbents

### 2. Official source refresh

Purpose:

- refresh official bios
- refresh portraits
- refresh office hierarchy
- refresh committees and district data

### 3. Event sync

Purpose:

- collect Taiwan-related events from official, media, and social sources
- attach participants
- merge duplicate event coverage

### 4. Legislation sync

Purpose:

- ingest Taiwan-related bills
- update official bill links
- update sponsor relationships

### 5. Background enrichment

Purpose:

- fill missing background fields
- improve Chinese names
- improve social profiles

## Near-Term Priorities

## 1. Complete official state sources

Continue adding official sources state by state.

Do not go back to Wikipedia as the primary state roster source.

Wikipedia remains:

- supplemental
- fallback
- enrichment only

## 2. Continue Taiwan event accumulation

Keep adding 2021 onward Taiwan-related events for:

- current officials
- former Biden-era officials
- current legislators
- former legislators
- state officials
- governors
- state legislators where relevant

## 3. Strengthen people pages

The person page should remain the main entry point.

It should show:

- background
- social links
- official pages
- recent Taiwan-related events
- legislation involvement
- committees
- office history

## 4. Improve official legislation enrichment

For Congress bills:

- keep Excel seed coverage
- gradually add `Congress.gov` links
- gradually improve sponsor linkage

For state legislation:

- use official state legislative sites wherever available

## Practical Rule for Ongoing Development

When adding any new source, ask:

1. Is it official?
2. Does it improve the people page?
3. Does it improve Taiwan-event coverage?
4. Can it run repeatedly with low cleanup cost?

If yes, it belongs in the system.

If not:

- keep it as supplemental only
- or defer it

## Output Standard

The system should ultimately let the user do four things easily:

1. Find a person
2. See that person’s official role and background
3. See Taiwan-related events involving that person
4. See Taiwan-related legislation connected to that person

That is the product direction.
