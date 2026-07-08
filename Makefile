.PHONY: video video-offline video-osm clean

video:
	python3 render_video.py --basemap satellite -o output/encounter_satellite.mp4

video-offline:
	python3 render_video.py --basemap offline -o output/encounter_offline.mp4

video-osm:
	python3 render_video.py --basemap osm -o output/encounter_osm.mp4

clean:
	rm -rf output/*.mp4
