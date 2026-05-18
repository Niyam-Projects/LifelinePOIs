COPY (
  WITH raw AS (
    SELECT type, id, tags, geometry
    FROM '{{INPUT}}'
    WHERE (
      -- Rail: stations, yards, major junctions, subway/metro stations
      tags['railway'] IN (
        'station', 'halt', 'yard', 'junction', 'facility',
        'tram_stop', 'subway_entrance', 'workshop'
      )
      -- Aviation: airports (all classes), terminals, heliports
      OR tags['aeroway'] IN (
        'aerodrome', 'terminal', 'helipad', 'heliport', 'navigationaid'
      )
      -- Maritime: ferry terminals, ports, docks
      OR tags['amenity'] IN ('ferry_terminal', 'bus_station')
      OR tags['harbour'] IS NOT NULL
      OR (tags['waterway'] IN ('dock', 'terminal') AND tags['name'] IS NOT NULL)
      -- Road services: interstate rest areas, truck plazas, motorway services
      OR tags['highway'] IN ('rest_area', 'services', 'bus_stop')
         AND tags['name'] IS NOT NULL
      OR tags['amenity'] = 'truck_stop'
      -- Public transit stops/stations
      OR (tags['public_transport'] IN ('station', 'stop_area')
          AND tags['name'] IS NOT NULL)
    )
    -- Filter out bare tram/bus stops without names (too granular)
    AND NOT (
      tags['railway'] IN ('tram_stop')
      AND tags['name'] IS NULL
    )
    AND NOT (
      tags['highway'] = 'bus_stop'
      AND tags['name'] IS NULL
    )
    AND (
      kind = 'node'
      OR (kind = 'area' AND (type = 'relation' OR tags['area'] = 'yes'
          OR tags['aeroway'] IN ('aerodrome', 'terminal', 'heliport')
          OR tags['railway'] IN ('station', 'yard', 'facility')
          OR tags['harbour'] IS NOT NULL
          OR tags['amenity'] IN ('ferry_terminal', 'bus_station', 'truck_stop')))
    )
  )
  SELECT
    type,
    id,
    tags['railway']                     AS railway,
    tags['aeroway']                     AS aeroway,
    tags['amenity']                     AS amenity,
    tags['highway']                     AS highway,
    tags['harbour']                     AS harbour,
    tags['waterway']                    AS waterway,
    tags['public_transport']            AS public_transport,
    tags['operator']                    AS operator,
    tags['operator:wikidata']           AS "operator:wikidata",
    tags['name']                        AS name,
    tags['ref']                         AS ref,
    tags['iata']                        AS iata,
    tags['icao']                        AS icao,
    tags['network']                     AS network,
    tags['route_ref']                   AS route_ref,
    tags['service']                     AS service,
    tags['aerodrome:type']              AS "aerodrome:type",
    tags['aerodrome:traffic']           AS "aerodrome:traffic",
    tags['ele']                         AS ele,
    tags['height']                      AS height,
    tags['capacity']                    AS capacity,
    tags['gauge']                       AS gauge,
    tags['electrified']                 AS electrified,
    tags['usage']                       AS usage,
    tags['supervised']                  AS supervised,
    tags['mooring']                     AS mooring,
    tags['addr:city']                   AS "addr:city",
    tags['addr:state']                  AS "addr:state",
    tags['addr:postcode']               AS "addr:postcode",
    tags['start_date']                  AS start_date,
    tags['wikipedia']                   AS wikipedia,
    tags['wikidata']                    AS wikidata,
    tags['access']                      AS access,
    prefix_map('name:', tags)           AS names,
    {
      xmin: ST_XMin(geometry)::FLOAT,
      ymin: ST_YMin(geometry)::FLOAT,
      xmax: ST_XMax(geometry)::FLOAT,
      ymax: ST_YMax(geometry)::FLOAT
    } AS bbox,
    geometry
  FROM raw
) TO '{{OUTPUT}}' WITH (FORMAT PARQUET, COMPRESSION ZSTD);
