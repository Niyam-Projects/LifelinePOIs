COPY (
  WITH raw AS (
    SELECT type, id, tags, kind, geometry
    FROM '{{INPUT}}'
    WHERE (
      tags['amenity'] IN (
        'hospital', 'clinic', 'pharmacy', 'nursing_home',
        'doctors', 'dentist', 'blood_bank', 'social_facility',
        'veterinary', 'dialysis'
      )
      OR tags['healthcare'] IS NOT NULL
      OR tags['social_facility'] IN (
        'nursing_home', 'assisted_living', 'group_home', 'medical_care',
        'hospice', 'rehabilitation'
      )
      OR tags['building'] IN ('hospital', 'clinic')
    )
    -- Exclude pure healthcare=yes catch-all on non-amenity features (too broad)
    AND NOT (
      tags['amenity'] IS NULL
      AND tags['healthcare'] = 'yes'
      AND tags['name'] IS NULL
    )
    AND (
      kind = 'node'
      OR (kind = 'area' AND (type = 'relation' OR tags['area'] = 'yes'
          OR tags['amenity'] IN ('hospital', 'clinic', 'nursing_home')
          OR tags['healthcare'] IN ('hospital', 'clinic', 'dialysis', 'rehabilitation')
          OR tags['building'] IN ('hospital', 'clinic')))
    )
  )
  SELECT
    type,
    id,
    tags['amenity']                     AS amenity,
    tags['healthcare']                  AS healthcare,
    tags['social_facility']             AS social_facility,
    tags['building']                    AS building,
    tags['operator']                    AS operator,
    tags['operator:wikidata']           AS "operator:wikidata",
    tags['name']                        AS name,
    tags['ref']                         AS ref,
    tags['phone']                       AS phone,
    tags['email']                       AS email,
    tags['website']                     AS website,
    tags['opening_hours']               AS opening_hours,
    tags['beds']                        AS beds,
    tags['capacity']                    AS capacity,
    tags['emergency']                   AS emergency,
    tags['speciality']                  AS speciality,
    tags['healthcare:speciality']       AS "healthcare:speciality",
    tags['addr:street']                 AS "addr:street",
    tags['addr:housenumber']            AS "addr:housenumber",
    tags['addr:city']                   AS "addr:city",
    tags['addr:state']                  AS "addr:state",
    tags['addr:postcode']               AS "addr:postcode",
    tags['level']                       AS level,
    tags['wheelchair']                  AS wheelchair,
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
    -- Pure building=hospital/clinic closed ways become centroid points.
    -- Features that also carry amenity= or healthcare= keep their polygon
    -- geometry so campus collapse can use them as boundary polygons.
    CASE
      WHEN kind = 'area'
       AND tags['building'] IN ('hospital', 'clinic')
       AND tags['amenity'] IS NULL
       AND tags['healthcare'] IS NULL
      THEN ST_Centroid(geometry)
      ELSE geometry
    END AS geometry
  FROM raw
) TO '{{OUTPUT}}' WITH (FORMAT PARQUET, COMPRESSION ZSTD);
