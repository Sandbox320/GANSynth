import tensorflow as tf
import numpy as np
import spectral_ops
import functools
import os


class NSynth(object):

    def __init__(self, audio_length, pitches, spectrogram_shape,
                 overlap, sample_rate, mel_downscale, data_format):

        self.audio_length = audio_length
        self.pitches = pitches
        self.spectrogram_shape = spectrogram_shape
        self.overlap = overlap
        self.sample_rate = sample_rate
        self.mel_downscale = mel_downscale
        self.data_format = data_format
        self.index_table = tf.contrib.lookup.index_table_from_tensor(
            mapping=sorted(pitches),
            dtype=tf.int32
        )

    def parse_example(self, example):
        # =========================================================================================
        # reference: https://magenta.tensorflow.org/datasets/nsynth
        features = tf.parse_single_example(
            serialized=example,
            features={
                "audio": tf.FixedLenFeature([self.audio_length], dtype=tf.float32),
                "pitch": tf.FixedLenFeature([], dtype=tf.int64),
            }
        )
        # =========================================================================================
        # wave
        wave = features["audio"]
        # =========================================================================================
        # one-hot label
        label = features["pitch"]
        label = self.index_table.lookup(label)
        label = tf.one_hot(label, len(self.pitches))

        return wave, label

    def preprocess(self, waves, labels):
        # =========================================================================================
        time_steps, num_freq_bins = self.spectrogram_shape
        # power of two only has 1 nonzero in binary representation
        if not bin(num_freq_bins).count("1") == 1:
            raise ValueError(
                "Wrong spectrogram_shape. Number of frequency bins must be "
                "a power of 2, not {}".format(num_freq_bins)
            )
        # trim the Nyquist frequency
        frame_length = num_freq_bins * 2
        frame_step = int((1. - self.overlap) * frame_length)
        # =========================================================================================
        # For Nsynth dataset, we are putting all padding in the front
        # This causes edge effects in the tail
        num_samples = frame_step * (time_steps - 1) + frame_length
        if num_samples < self.audio_length:
            raise ValueError(
                "Wrong audio length. Number of STFT samples {} should be "
                "greater equal audio lengeth {}.".format(num_samples, self.audio_length)
            )
        padding = num_samples - self.audio_length
        padding_left = padding
        padding_right = padding - padding_left
        # =========================================================================================
        # convert from waves to complex stfts
        # wave: tensor of the waveform, shape [batch, time]
        # stft: complex64 tensor of stft, shape [batch, time, freq]
        waves = tf.pad(
            tensor=waves,
            paddings=[[0, 0], [padding_left, padding_right]]
        )
        stfts = tf.contrib.signal.stft(
            signals=waves,
            frame_length=frame_length,
            frame_step=frame_step,
            fft_length=frame_length,
            pad_end=False
        )[:, :, 1:]
        stft_shape = stfts.shape.as_list()[1:]
        if stft_shape != self.spectrogram_shape:
            raise ValueError(
                "Spectrogram returned the wrong shape {}, is not the same as the "
                "constructor spectrogram_shape {}.".format(stft_shape, self.spectrogram_shape)
            )
        # =========================================================================================
        # converts stft to mel spectrogram
        # stft: complex64 tensor of stft
        # shape [batch, time, freq]
        # mel spectrogram: tensor of log magnitudes and instantaneous frequencies
        # shape [batch, time, freq, 2], mel scaling of frequencies
        magnitude_spectrograms = tf.abs(stfts)
        phase_angles = tf.angle(stfts)

        linear_to_mel_weight_matrix = tf.contrib.signal.linear_to_mel_weight_matrix(
            num_mel_bins=num_freq_bins // self.mel_downscale,
            num_spectrogram_bins=num_freq_bins,
            sample_rate=self.sample_rate,
            lower_edge_hertz=0.0,
            upper_edge_hertz=self.sample_rate / 2.0
        )
        mel_magnitude_spectrograms = tf.tensordot(
            a=magnitude_spectrograms,
            b=linear_to_mel_weight_matrix,
            axes=1
        )
        mel_magnitude_spectrograms.set_shape(
            magnitude_spectrograms.shape[:-1].concatenate(
                linear_to_mel_weight_matrix.shape[-1:]
            )
        )
        mel_phase_angles = tf.tensordot(
            a=phase_angles,
            b=linear_to_mel_weight_matrix,
            axes=1
        )
        mel_phase_angles.set_shape(
            phase_angles.shape[:-1].concatenate(
                linear_to_mel_weight_matrix.shape[-1:]
            )
        )

        log_mel_magnitude_spectrograms = tf.log(mel_magnitude_spectrograms + 1e-6)
        mel_instantaneous_frequencies = spectral_ops.instantaneous_frequency(mel_phase_angles)

        data = tf.concat([
            tf.expand_dims(log_mel_magnitude_spectrograms, axis=-1),
            tf.expand_dims(mel_instantaneous_frequencies, axis=-1)
        ], axis=-1)

        if self.data_format == "channels_first":
            data = tf.transpose(data, [0, 3, 1, 2])

        return data, labels

    def input_fn(self, filenames, batch_size, num_epochs, shuffle):

        dataset = tf.data.TFRecordDataset(filenames)
        if shuffle:
            dataset = dataset.shuffle(
                buffer_size=sum([
                    len(list(tf.python_io.tf_record_iterator(filename)))
                    for filename in filenames
                ]),
                reshuffle_each_iteration=True
            )
        dataset = dataset.repeat(count=num_epochs)
        dataset = dataset.map(
            map_func=self.parse_example,
            num_parallel_calls=os.cpu_count()
        )
        dataset = dataset.batch(batch_size=batch_size)
        dataset = dataset.map(
            map_func=self.preprocess,
            num_parallel_calls=os.cpu_count()
        )
        dataset = dataset.prefetch(buffer_size=1)

        iterator = dataset.make_initializable_iterator()
        tf.add_to_collection(tf.GraphKeys.TABLE_INITIALIZERS, iterator.initializer)

        return iterator.get_next()
